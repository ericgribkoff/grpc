#!/usr/bin/env python
# Copyright 2020 gRPC authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Run xDS integration tests on GCP using Traffic Director."""

import argparse
import googleapiclient.discovery
import grpc
import logging
import os
import random
import shlex
import socket
import subprocess
import sys
import tempfile
import time

from oauth2client.client import GoogleCredentials

from src.proto.grpc.testing import messages_pb2
from src.proto.grpc.testing import test_pb2_grpc

logger = logging.getLogger()
console_handler = logging.StreamHandler()
logger.addHandler(console_handler)


def parse_port_range(port_arg):
    try:
        port = int(port_arg)
        return range(port, port + 1)
    except:
        port_min, port_max = port_arg.split(':')
        return range(int(port_min), int(port_max) + 1)


argp = argparse.ArgumentParser(description='Run xDS interop tests on GCP')
argp.add_argument('--project_id', help='GCP project id')
argp.add_argument(
    '--gcp_suffix',
    default='',
    help='Optional suffix for all generated GCP resource names. Useful to ensure '
    'distinct names across test runs.')
argp.add_argument(
    '--test_case',
    default=None,
    choices=[
        'all',
        'backends_restart',
        'change_backend_service',
        'new_instance_group_receives_traffic',
        'ping_pong',
        'remove_instance_group',
        'round_robin',
        'secondary_locality_gets_requests_on_primary_failure',
        'secondary_locality_gets_no_requests_on_partial_primary_failure',
    ])
argp.add_argument(
    '--client_cmd',
    default=None,
    help='Command to launch xDS test client. This script will fill in '
    '{service_host}, {service_port},{stats_port} and {qps} parameters using '
    'str.format(), and generate the GRPC_XDS_BOOTSTRAP file.')
argp.add_argument('--zone', default='us-central1-a')
argp.add_argument('--qps', default=10, help='Client QPS')
argp.add_argument(
    '--wait_for_backend_sec',
    default=900,
    help='Time limit for waiting for created backend services to report healthy '
    'when launching test suite')
argp.add_argument(
    '--keep_gcp_resources',
    default=False,
    action='store_true',
    help=
    'Leave GCP VMs and configuration running after test. Default behavior is '
    'to delete when tests complete.')
argp.add_argument(
    '--compute_discovery_document',
    default=None,
    type=str,
    help=
    'If provided, uses this file instead of retrieving via the GCP discovery API'
)
argp.add_argument('--network',
                  default='global/networks/default',
                  help='GCP network to use')
argp.add_argument('--service_port_range',
                  default='8080:8180',
                  type=parse_port_range,
                  help='Listening port for created gRPC backends. Specified as '
                  'either a single int or as a range in the format min:max, in '
                  'which case an available port p will be chosen s.t. min <= p '
                  '<= max')
argp.add_argument(
    '--stats_port',
    default=8079,
    type=int,
    help='Local port for the client process to expose the LB stats service')
argp.add_argument('--xds_server',
                  default='trafficdirector.googleapis.com:443',
                  help='xDS server')
argp.add_argument('--source_image',
                  default='projects/debian-cloud/global/images/family/debian-9',
                  help='Source image for VMs created during the test')
argp.add_argument(
    '--tolerate_gcp_errors',
    default=False,
    action='store_true',
    help=
    'Continue with test even when an error occurs during setup. Intended for '
    'manual testing, where attempts to recreate any GCP resources already '
    'existing will result in an error')
argp.add_argument('--verbose',
                  help='verbose log output',
                  default=False,
                  action='store_true')
args = argp.parse_args()

if args.verbose:
    logger.setLevel(logging.DEBUG)

PROJECT_ID = args.project_id
ZONE = args.zone
QPS = args.qps
TEST_CASE = args.test_case
CLIENT_CMD = args.client_cmd
WAIT_FOR_BACKEND_SEC = args.wait_for_backend_sec
TEMPLATE_NAME = 'test-template' + args.gcp_suffix
INSTANCE_GROUP_NAME = 'test-ig' + args.gcp_suffix
HEALTH_CHECK_NAME = 'test-hc' + args.gcp_suffix
FIREWALL_RULE_NAME = 'test-fw-rule' + args.gcp_suffix
BACKEND_SERVICE_NAME = 'test-backend-service' + args.gcp_suffix
URL_MAP_NAME = 'test-map' + args.gcp_suffix
SERVICE_HOST = 'grpc-test' + args.gcp_suffix
TARGET_PROXY_NAME = 'test-target-proxy' + args.gcp_suffix
FORWARDING_RULE_NAME = 'test-forwarding-rule' + args.gcp_suffix
KEEP_GCP_RESOURCES = args.keep_gcp_resources
TOLERATE_GCP_ERRORS = args.tolerate_gcp_errors
STATS_PORT = args.stats_port
INSTANCE_GROUP_SIZE = 2
WAIT_FOR_OPERATION_SEC = 60
NUM_TEST_RPCS = 10 * QPS
WAIT_FOR_STATS_SEC = 30
BOOTSTRAP_TEMPLATE = """
{{
  "node": {{
    "id": "{node_id}"
  }},
  "xds_servers": [{{
    "server_uri": "%s",
    "channel_creds": [
      {{
        "type": "google_default",
        "config": {{}}
      }}
    ]
  }}]
}}""" % args.xds_server
#ZONES = ['us-east1-c', 'us-central1-a', 'us-west1-b']


def get_client_stats(num_rpcs, timeout_sec):
    with grpc.insecure_channel('localhost:%d' % STATS_PORT) as channel:
        stub = test_pb2_grpc.LoadBalancerStatsServiceStub(channel)
        request = messages_pb2.LoadBalancerStatsRequest()
        request.num_rpcs = num_rpcs
        request.timeout_sec = timeout_sec
        rpc_timeout = timeout_sec * 2  # Allow time for connection establishment
        try:
            response = stub.GetClientStats(request,
                                           wait_for_ready=True,
                                           timeout=rpc_timeout)
            logger.debug('Invoked GetClientStats RPC: %s', response)
            return response
        except grpc.RpcError as rpc_error:
            raise Exception('GetClientStats RPC failed')


def wait_until_only_given_backends_receive_load(backends,
                                                timeout_sec,
                                                min_rpcs=1,
                                                no_failures=False):
    start_time = time.time()
    error_msg = None
    print('starting to wait for ', timeout_sec, ' until backends', backends,
          ' receive load')
    print('start time:', start_time)
    while time.time() - start_time <= timeout_sec:
        error_msg = None
        stats = get_client_stats(max(len(backends), min_rpcs), timeout_sec)
        rpcs_by_peer = stats.rpcs_by_peer
        for backend in backends:
            if backend not in rpcs_by_peer:
                error_msg = 'Backend %s did not receive load' % backend
                break
        if not error_msg and len(rpcs_by_peer) > len(backends):
            error_msg = 'Unexpected backend received load: %s' % rpcs_by_peer
        if no_failures and stats.num_failures > 0:
            error_msg = '%d RPCs failed' % stats.num_failures
        if not error_msg:
            return
    print('end time:', time.time())
    raise Exception(error_msg)


# Takes approx 6 minutes
def test_backends_restart(compute, project, zone, instance_names,
                          instance_group_name, backend_service_name,
                          instance_group_url, num_rpcs, stats_timeout_sec):
    start_time = time.time()
    wait_until_only_given_backends_receive_load(instance_names,
                                                stats_timeout_sec)
    stats = get_client_stats(100, stats_timeout_sec)
    result = compute.instanceGroupManagers().resize(
        project=project,
        zone=zone,
        instanceGroupManager=instance_group_name,
        size=0).execute()
    wait_for_zone_operation(compute,
                            project,
                            zone,
                            result['name'],
                            timeout_sec=360)
    # for instance in instance_names:
    #   # Instances in MIG auto-heal
    #   stop_instance(compute, project, zone, instance)
    wait_until_only_given_backends_receive_load([], 600)
    result = compute.instanceGroupManagers().resize(
        project=project,
        zone=zone,
        instanceGroupManager=instance_group_name,
        size=len(instance_names)).execute()
    wait_for_zone_operation(compute,
                            project,
                            zone,
                            result['name'],
                            timeout_sec=600)
    wait_for_healthy_backends(compute, project, backend_service_name,
                              instance_group_url, 600)
    new_instance_names = get_instance_names(compute, project, zone,
                                            instance_group_name)
    # for instance in instance_names:
    #   start_instance(compute, project, zone, instance)
    wait_until_only_given_backends_receive_load(new_instance_names, 600)
    new_stats = get_client_stats(100, stats_timeout_sec)
    for i in range(len(instance_names)):
        # fix
        if abs(stats.rpcs_by_peer[instance_names[i]] -
               new_stats.rpcs_by_peer[new_instance_names[i]]) > 1:
            raise Exception('outside of threshold for ', new_instance_names[i],
                            stats.rpcs_by_peer[instance_names[i]],
                            new_stats.rpcs_by_peer[new_instance_names[i]])


# TODO: put all created resource urls/names/zone etc into an object that is
# passed around + including compute and project
def test_change_backend_service(
    compute, project, zone, backend_service_name, backend_service_url,
    url_map_name, primary_instance_group_name, primary_instance_group_url,
    primary_instance_names, service_port, template_url, health_check_url,
    stats_timeout_sec):
    secondary_instance_group_name = 'test-secondary-same-locality-ig' + args.gcp_suffix
    try:
        secondary_instance_group_url = create_instance_group(
            compute, project, zone, secondary_instance_group_name, 2,
            service_port, template_url)
    except googleapiclient.errors.HttpError as http_error:
        #TODO: handle this elsewhere
        result = compute.instanceGroups().get(
            project=project,
            zone=zone,
            instanceGroup=secondary_instance_group_name).execute()
        secondary_instance_group_url = result['selfLink']
    new_backend_service_name = 'test-secondary-backend-service' + args.gcp_suffix
    try:
        new_backend_service_url = create_backend_service(
            compute, project, new_backend_service_name, health_check_url)
    except googleapiclient.errors.HttpError as http_error:
        result = compute.backendServices().get(
            project=project, backendService=new_backend_service_name).execute()
        new_backend_service_url = result['selfLink']
    add_instances_to_backend(compute, project, new_backend_service_name,
                             [secondary_instance_group_url])
    wait_for_healthy_backends(compute, project, new_backend_service_name,
                              secondary_instance_group_url,
                              WAIT_FOR_BACKEND_SEC)
    secondary_instance_names = get_instance_names(
        compute, project, zone, secondary_instance_group_name)

    wait_until_only_given_backends_receive_load(primary_instance_names,
                                                stats_timeout_sec,
                                                min_rpcs=100)

    path_matcher_name = 'path-matcher'
    config = {
        'defaultService':
            new_backend_service_url,
        'pathMatchers': [{
            'name': path_matcher_name,
            'defaultService': new_backend_service_url,
        }]
    }
    result = compute.urlMaps().patch(project=project,
                                     urlMap=url_map_name,
                                     body=config).execute()
    wait_for_global_operation(compute, project, result['name'])

    stats = get_client_stats(500, 100)
    if stats.num_failures > 0:
        raise Exception('Unexpected failure: %s', stats)
    wait_until_only_given_backends_receive_load(secondary_instance_names,
                                                stats_timeout_sec,
                                                min_rpcs=100,
                                                no_failures=True)

    if not args.keep_gcp_resources:
        delete_instance_group(compute, project, zone,
                              secondary_instance_group_name)
    else:
        add_instances_to_backend(
            compute, project, backend_service_name,
            [primary_instance_group_url, secondary_instance_group_url])
        path_matcher_name = 'path-matcher'
        config = {
            'defaultService':
                backend_service_url,
            'pathMatchers': [{
                'name': path_matcher_name,
                'defaultService': backend_service_url,
            }]
        }
        result = compute.urlMaps().patch(project=project,
                                         urlMap=url_map_name,
                                         body=config).execute()
        wait_for_global_operation(compute, project, result['name'])


# TODO: rate balancing mode
def test_new_instance_group_receives_traffic(
    compute, project, zone, backend_service_name, primary_instance_group_name,
    primary_instance_group_url, primary_instance_names, service_port,
    template_url, stats_timeout_sec):
    wait_until_only_given_backends_receive_load(primary_instance_names,
                                                stats_timeout_sec,
                                                min_rpcs=100)
    secondary_instance_group_name = 'test-secondary-same-locality-ig' + args.gcp_suffix
    try:
        secondary_instance_group_url = create_instance_group(
            compute, project, zone, secondary_instance_group_name, 2,
            service_port, template_url)
    except googleapiclient.errors.HttpError as http_error:
        #TODO: handle this elsewhere
        result = compute.instanceGroups().get(
            project=project,
            zone=zone,
            instanceGroup=secondary_instance_group_name).execute()
        secondary_instance_group_url = result['selfLink']
    # this uses patch, which replaces the old instance group
    add_instances_to_backend(
        compute, project, backend_service_name,
        [primary_instance_group_url, secondary_instance_group_url])
    wait_for_healthy_backends(compute, project, backend_service_name,
                              secondary_instance_group_url,
                              WAIT_FOR_BACKEND_SEC)
    secondary_instance_names = get_instance_names(
        compute, project, zone, secondary_instance_group_name)
    wait_until_only_given_backends_receive_load(primary_instance_names +
                                                secondary_instance_names,
                                                stats_timeout_sec,
                                                min_rpcs=100)
    stats = get_client_stats(100, 20)

    if not args.keep_gcp_resources:
        delete_instance_group(compute, project, zone,
                              secondary_instance_group_name)


def test_ping_pong(gcp):
    instance_names = get_instance_names(gcp, gcp.instance_groups[0])
    start_time = time.time()
    error_msg = None
    while time.time() - start_time <= WAIT_FOR_STATS_SEC:
        error_msg = None
        stats = get_client_stats(NUM_TEST_RPCS, WAIT_FOR_STATS_SEC)
        rpcs_by_peer = stats.rpcs_by_peer
        for backend in backends:
            if backend not in rpcs_by_peer:
                error_msg = 'Backend %s did not receive load' % backend
                break
        if not error_msg and len(rpcs_by_peer) > len(backends):
            error_msg = 'Unexpected backend received load: %s' % rpcs_by_peer
        if not error_msg:
            return
    raise Exception(error_msg)


def test_remove_instance_group(compute, project, zone, backend_service_name,
                               primary_instance_group_name,
                               primary_instance_group_url,
                               primary_instance_names, service_port,
                               template_url, stats_timeout_sec):
    secondary_instance_group_name = 'test-secondary-same-locality-ig' + args.gcp_suffix
    try:
        secondary_instance_group_url = create_instance_group(
            compute, project, zone, secondary_instance_group_name, 2,
            service_port, template_url)
    except googleapiclient.errors.HttpError as http_error:
        #TODO: handle this elsewhere
        result = compute.instanceGroups().get(
            project=project,
            zone=zone,
            instanceGroup=secondary_instance_group_name).execute()
        secondary_instance_group_url = result['selfLink']
    # this uses patch, which replaces the old instance group
    add_instances_to_backend(
        compute, project, backend_service_name,
        [primary_instance_group_url, secondary_instance_group_url])
    wait_for_healthy_backends(compute, project, backend_service_name,
                              secondary_instance_group_url,
                              WAIT_FOR_BACKEND_SEC)
    secondary_instance_names = get_instance_names(
        compute, project, zone, secondary_instance_group_name)
    wait_until_only_given_backends_receive_load(primary_instance_names +
                                                secondary_instance_names,
                                                stats_timeout_sec,
                                                min_rpcs=100)
    stats = get_client_stats(100, 20)
    add_instances_to_backend(compute, project, backend_service_name,
                             [secondary_instance_group_url])
    stats = get_client_stats(500, 100)
    if stats.num_failures > 0:
        raise Exception('Unexpected failure: %s', stats)
    wait_until_only_given_backends_receive_load(secondary_instance_names,
                                                stats_timeout_sec,
                                                min_rpcs=100,
                                                no_failures=True)
    if not args.keep_gcp_resources:
        delete_instance_group(compute, project, zone,
                              secondary_instance_group_name)
    else:
        add_instances_to_backend(compute, project, backend_service_name,
                                 [primary_instance_group_url])


def test_round_robin(backends, num_rpcs, stats_timeout_sec):
    threshold = 1
    wait_until_only_given_backends_receive_load(backends, stats_timeout_sec)
    stats = get_client_stats(num_rpcs, stats_timeout_sec)
    requests_received = [stats.rpcs_by_peer[x] for x in stats.rpcs_by_peer]
    total_requests_received = sum(
        [stats.rpcs_by_peer[x] for x in stats.rpcs_by_peer])
    if total_requests_received != num_rpcs:
        raise Exception('Unexpected RPC failures', stats)
    expected_requests = total_requests_received / len(backends)
    for backend in backends:
        if abs(stats.rpcs_by_peer[backend] - expected_requests) > threshold:
            raise Exception(
                'RPC peer distribution differs from expected by more than %d for backend %s (%s)',
                threshold, backend, stats)


def test_secondary_locality_gets_no_requests_on_partial_primary_failure(
    compute, project, primary_zone, backend_service_name,
    primary_instance_group_name, primary_instance_group_url,
    primary_instance_names, service_port, template_url, stats_timeout_sec):
    secondary_zone = 'us-west1-b'  # In same region is not secondary, gets traffic 'us-central1-b'
    # TODO: name will conflict with other secondary locality test, should
    # reuse same secondary locality when test_case=all
    secondary_instance_group_name = 'test-secondary-ig' + args.gcp_suffix
    try:
        secondary_instance_group_url = create_instance_group(
            compute, project, secondary_zone, secondary_instance_group_name, 2,
            service_port, template_url)
    except googleapiclient.errors.HttpError as http_error:
        #TODO: handle this elsewhere
        result = compute.instanceGroups().get(
            project=project,
            zone=secondary_zone,
            instanceGroup=secondary_instance_group_name).execute()
        secondary_instance_group_url = result['selfLink']
    # this uses patch, which replaces the old instance group
    add_instances_to_backend(
        compute, project, backend_service_name,
        [primary_instance_group_url, secondary_instance_group_url])
    wait_for_healthy_backends(compute, project, backend_service_name,
                              secondary_instance_group_url,
                              WAIT_FOR_BACKEND_SEC)
    secondary_instance_names = get_instance_names(
        compute, project, secondary_zone, secondary_instance_group_name)
    wait_until_only_given_backends_receive_load(primary_instance_names,
                                                stats_timeout_sec,
                                                min_rpcs=100)
    stats = get_client_stats(100, 20)
    secondary_instances_with_load = [
        i for i in secondary_instance_names if i in stats.rpcs_by_peer
    ]
    if secondary_instances_with_load:
        raise Exception('Unexpected RPCs to secondary instances: %s', stats)
    result = compute.instanceGroupManagers().resize(
        project=project,
        zone=primary_zone,
        instanceGroupManager=primary_instance_group_name,
        size=1).execute()
    wait_for_zone_operation(compute,
                            project,
                            primary_zone,
                            result['name'],
                            timeout_sec=360)
    start_time = time.time()
    while True:
        remaining_primary_instance_names = get_instance_names(
            compute, project, primary_zone, primary_instance_group_name)
        if len(remaining_primary_instance_names) < len(primary_instance_names):
            break
        if time.time() - start_time > 360:
            raise Exception('Failed to resize primary instance group')
        time.sleep(1)
    # for instance in instance_names:
    #   # Instances in MIG auto-heal
    #   stop_instance(compute, project, zone, instance)
    wait_until_only_given_backends_receive_load(
        remaining_primary_instance_names, 600, min_rpcs=100, no_failures=True)
    stats = get_client_stats(100, 20)  # not really necessary
    secondary_instances_with_load = [
        i for i in secondary_instance_names if i in stats.rpcs_by_peer
    ]
    if secondary_instances_with_load:
        raise Exception('Unexpected RPCs to secondary instances: %s', stats)
    # needs to be in finally
    if not args.keep_gcp_resources:
        delete_instance_group(compute, project, secondary_zone,
                              secondary_instance_group_name)
    else:
        #TODO  cleanup
        result = compute.instanceGroupManagers().resize(
            project=project,
            zone=primary_zone,
            instanceGroupManager=primary_instance_group_name,
            size=len(primary_instance_names)).execute()
        wait_for_zone_operation(compute,
                                project,
                                primary_zone,
                                result['name'],
                                timeout_sec=360)


def test_secondary_locality_gets_requests_on_primary_failure(
    compute, project, primary_zone, backend_service_name,
    primary_instance_group_name, primary_instance_group_url,
    primary_instance_names, service_port, template_url, stats_timeout_sec):
    # two MIGs, one in same zone as client, one in another zone
    # wait for all backends healthy
    # resize MIG in same zone to 0
    secondary_zone = 'us-west1-b'  # In same region is not secondary, gets traffic 'us-central1-b'
    secondary_instance_group_name = 'test-secondary-ig' + args.gcp_suffix
    try:
        secondary_instance_group_url = create_instance_group(
            compute, project, secondary_zone, secondary_instance_group_name, 2,
            service_port, template_url)
    except googleapiclient.errors.HttpError as http_error:
        #TODO: handle this elsewhere
        result = compute.instanceGroups().get(
            project=project,
            zone=secondary_zone,
            instanceGroup=secondary_instance_group_name).execute()
        secondary_instance_group_url = result['selfLink']
    # this uses patch, which replaces the old instance group
    add_instances_to_backend(
        compute, project, backend_service_name,
        [primary_instance_group_url, secondary_instance_group_url])
    wait_for_healthy_backends(compute, project, backend_service_name,
                              secondary_instance_group_url,
                              WAIT_FOR_BACKEND_SEC)
    secondary_instance_names = get_instance_names(
        compute, project, secondary_zone, secondary_instance_group_name)
    wait_until_only_given_backends_receive_load(primary_instance_names,
                                                stats_timeout_sec,
                                                min_rpcs=100)
    stats = get_client_stats(100, 20)
    secondary_instances_with_load = [
        i for i in secondary_instance_names if i in stats.rpcs_by_peer
    ]
    if secondary_instances_with_load:
        raise Exception('Unexpected RPCs to secondary instances: %s', stats)
    result = compute.instanceGroupManagers().resize(
        project=project,
        zone=primary_zone,
        instanceGroupManager=primary_instance_group_name,
        size=0).execute()
    wait_for_zone_operation(compute,
                            project,
                            primary_zone,
                            result['name'],
                            timeout_sec=360)
    # for instance in instance_names:
    #   # Instances in MIG auto-heal
    #   stop_instance(compute, project, zone, instance)
    wait_until_only_given_backends_receive_load(secondary_instance_names,
                                                600,
                                                min_rpcs=100)
    stats = get_client_stats(100, 20)  # not really necessary
    result = compute.instanceGroupManagers().resize(
        project=project,
        zone=primary_zone,
        instanceGroupManager=primary_instance_group_name,
        size=len(primary_instance_names)).execute()
    wait_for_zone_operation(compute,
                            project,
                            primary_zone,
                            result['name'],
                            timeout_sec=600)
    wait_for_healthy_backends(compute, project, backend_service_name,
                              primary_instance_group_url, 600)
    new_instance_names = get_instance_names(compute, project, primary_zone,
                                            primary_instance_group_name)
    # for instance in instance_names:
    #   start_instance(compute, project, zone, instance)
    wait_until_only_given_backends_receive_load(new_instance_names,
                                                600,
                                                min_rpcs=100)
    stats = get_client_stats(100, 20)
    secondary_instances_with_load = [
        i for i in secondary_instance_names if i in stats.rpcs_by_peer
    ]
    if secondary_instances_with_load:
        raise Exception('Unexpected RPCs to secondary instances: %s', stats)

    # TODO: clean up instance group
    if not args.keep_gcp_resources:
        delete_instance_group(compute, project, secondary_zone,
                              secondary_instance_group_name)


def create_instance_template(gcp, name, network, source_image):
    config = {
        'name': name + gcp.suffix,
        'properties': {
            'tags': {
                'items': ['grpc-allow-healthcheck']
            },
            'machineType': 'e2-standard-2',
            'serviceAccounts': [{
                'email': 'default',
                'scopes': ['https://www.googleapis.com/auth/cloud-platform',]
            }],
            'networkInterfaces': [{
                'accessConfigs': [{
                    'type': 'ONE_TO_ONE_NAT'
                }],
                'network': network
            }],
            'disks': [{
                'boot': True,
                'initializeParams': {
                    'sourceImage': source_image
                }
            }],
            'metadata': {
                'items': [{
                    'key':
                        'startup-script',
                    'value':
                        """#!/bin/bash
sudo apt update
sudo apt install -y git default-jdk
mkdir java_server
pushd java_server
git clone https://github.com/grpc/grpc-java.git
pushd grpc-java
pushd interop-testing
../gradlew installDist -x test -PskipCodegen=true -PskipAndroid=true

nohup build/install/grpc-interop-testing/bin/xds-test-server --port=%d 1>/dev/null &"""
                        % gcp.service_port
                }]
            }
        }
    }

    result = gcp.compute.instanceTemplates().insert(project=gcp.project,
                                                    body=config).execute()
    wait_for_global_operation(gcp, result['name'])
    gcp.instance_template = GcpResource(config['name'], result['targetLink'])


def add_instance_group(gcp, zone, name, size):
    config = {
        'name': name + gcp.suffix,
        'instanceTemplate': gpc.instance_template.url,
        'targetSize': size,
        'namedPorts': [{
            'name': 'grpc',
            'port': gcp.service_port
        }]
    }

    result = gcp.compute.instanceGroupManagers().insert(project=gcp.project,
                                                        zone=zone,
                                                        body=config).execute()
    wait_for_zone_operation(gcp, zone, result['name'])
    result = gcp.compute.instanceGroupManagers().get(
        project=gcp.project, zone=zone,
        instanceGroupManager=config['name']).execute()
    gcp.instance_groups.append(
        InstanceGroup(config['name'], result['instanceGroup'], zone))


def create_health_check(gcp, name):
    config = {
        'name': name + gcp.suffix,
        'type': 'TCP',
        'tcpHealthCheck': {
            'portName': 'grpc'
        }
    }
    result = gcp.compute.healthChecks().insert(project=gcp.project,
                                               body=config).execute()
    wait_for_global_operation(gcp, result['name'])
    gcp.health_check = GcpResource(config['name'], result['targetLink'])


def create_health_check_firewall_rule(gcp, name):
    config = {
        'name': name + gcp.suffix,
        'direction': 'INGRESS',
        'allowed': [{
            'IPProtocol': 'tcp'
        }],
        'sourceRanges': ['35.191.0.0/16', '130.211.0.0/22'],
        'targetTags': ['grpc-allow-healthcheck'],
    }
    result = gcp.compute.firewalls().insert(project=gcp.project,
                                            body=config).execute()
    wait_for_global_operation(gcp, result['name'])
    gcp.health_check_firewall_rule = GcpResource(config['name'],
                                                 result['targetLink'])


def add_backend_service(gcp, name):
    config = {
        'name': name + gcp.suffix,
        'loadBalancingScheme': 'INTERNAL_SELF_MANAGED',
        'healthChecks': [gcp.health_check.url],
        'portName': 'grpc',
        'protocol': 'HTTP2'
    }
    result = gcp.compute.backendServices().insert(project=gcp.project,
                                                  body=config).execute()
    wait_for_global_operation(gcp, result['name'])
    gcp.backend_services.append(
        GcpResources(config['name'], result['targetLink']))


def create_url_map(gcp, name, backend_service, host_name):
    path_matcher_name = 'path-matcher'  # TODO: extract
    config = {
        'name': name + gcp.suffix,
        'defaultService': backend_service.url,
        'pathMatchers': [{
            'name': path_matcher_name,
            'defaultService': backend_service.url,
        }],
        'hostRules': [{
            'hosts': [host_name],
            'pathMatcher': path_matcher_name
        }]
    }
    result = gcp.compute.urlMaps().insert(project=gcp.project,
                                          body=config).execute()
    wait_for_global_operation(gcp, result['name'])
    gcp.url_map = GcpResource(config['name'], result['targetLink'])


def create_target_http_proxy(gcp, name):
    config = {
        'name': name + gcp.suffix,
        'url_map': gcp.url_map.url,
    }
    result = gcp.compute.targetHttpProxies().insert(project=gcp.project,
                                                    body=config).execute()
    wait_for_global_operation(gcp, result['name'])
    gcp.target_http_proxy = GcpResource(config['name'], result['targetLink'])


def create_global_forwarding_rule(gcp, name, port):
    config = {
        'name': name + gcp.suffix,
        'loadBalancingScheme': 'INTERNAL_SELF_MANAGED',
        'portRange': str(port),
        'IPAddress': '0.0.0.0',
        'network': args.network,
        'target': gcp.target_http_proxy.url,
    }
    result = gcp.compute.globalForwardingRules().insert(project=gcp.project,
                                                        body=config).execute()
    wait_for_global_operation(gcp, result['name'])
    gcp.global_forwarding_rule = GcpResource(config['name'],
                                             result['targetLink'])


def delete_global_forwarding_rule(gcp):
    try:
        result = gcp.compute.globalForwardingRules().delete(
            project=gcp.project,
            forwardingRule=gcp.forwarding_rule.name).execute()
        wait_for_global_operation(gcp, result['name'])
    except googleapiclient.errors.HttpError as http_error:
        logger.info('Delete failed: %s', http_error)


def delete_target_http_proxy(gcp):
    try:
        result = gcp.compute.targetHttpProxies().delete(
            project=gcp.project,
            targetHttpProxy=gcp.target_http_proxy.name).execute()
        wait_for_global_operation(gcp, result['name'])
    except googleapiclient.errors.HttpError as http_error:
        logger.info('Delete failed: %s', http_error)


def delete_url_map(gcp):
    try:
        result = gcp.compute.urlMaps().delete(
            project=gcp.project, urlMap=gcp.url_map.name).execute()
        wait_for_global_operation(gcp, result['name'])
    except googleapiclient.errors.HttpError as http_error:
        logger.info('Delete failed: %s', http_error)


def delete_backend_services(gcp):
    for backend_service in gcp.backend_services:
        try:
            result = gcp.compute.backendServices().delete(
                project=gcp.project,
                backendService=backend_service.name).execute()
            wait_for_global_operation(gcp, result['name'])
        except googleapiclient.errors.HttpError as http_error:
            logger.info('Delete failed: %s', http_error)


def delete_firewall(gcp):
    try:
        result = gcp.compute.firewalls().delete(project=gcp.project,
                                                firewall=gcp,
                                                firewall_rule.name).execute()
        wait_for_global_operation(gcp, result['name'])
    except googleapiclient.errors.HttpError as http_error:
        logger.info('Delete failed: %s', http_error)


def delete_health_check(gcp):
    try:
        result = gcp.compute.healthChecks().delete(
            project=gcp.project, healthCheck=gcp.health_check.name).execute()
        wait_for_global_operation(gcp, result['name'])
    except googleapiclient.errors.HttpError as http_error:
        logger.info('Delete failed: %s', http_error)


def delete_instance_groups(gcp):
    for instance_group in gcp.instance_groups:
        try:
            result = gcp.compute.instanceGroupManagers().delete(
                project=gcp.project,
                zone=instance_group.zone,
                instanceGroupManager=instance_group.name).execute()
            timeout_sec = 180  # Deleting an instance group can be slow
            wait_for_zone_operation(gcp,
                                    instance_group.zone,
                                    result['name'],
                                    timeout_sec=timeout_sec)
        except googleapiclient.errors.HttpError as http_error:
            logger.info('Delete failed: %s', http_error)


def delete_instance_template(gcp):
    try:
        result = gcp.compute.instanceTemplates().delete(
            project=gcp.project,
            instanceTemplate=gcp.instance_template.name).execute()
        wait_for_global_operation(gcp, result['name'])
    except googleapiclient.errors.HttpError as http_error:
        logger.info('Delete failed: %s', http_error)


def add_instances_to_backend(gcp, backend_service, instance_groups):
    config = {
        'backends': [{
            'group': instance_group.url,
            'balancingMode': 'RATE',
            'maxRate': 1
        } for instance_group in instance_groups],
    }
    result = gcp.compute.backendServices().patch(
        project=gcp.project, backendService=backend_service.name,
        body=config).execute()
    wait_for_global_operation(gcp, result['name'])


def wait_for_global_operation(gcp,
                              operation,
                              timeout_sec=WAIT_FOR_OPERATION_SEC):
    start_time = time.time()
    while time.time() - start_time <= timeout_sec:
        result = gcp.compute.globalOperations().get(
            project=gcp.project, operation=operation).execute()
        if result['status'] == 'DONE':
            if 'error' in result:
                raise Exception(result['error'])
            return
        time.sleep(1)
    raise Exception('Operation %s did not complete within %d', operation,
                    timeout_sec)


def wait_for_zone_operation(gcp,
                            zone,
                            operation,
                            timeout_sec=WAIT_FOR_OPERATION_SEC):
    start_time = time.time()
    while time.time() - start_time <= timeout_sec:
        result = gcp.compute.zoneOperations().get(
            project=gcp.project, zone=zone, operation=operation).execute()
        # print(result)
        if result['status'] == 'DONE':
            if 'error' in result:
                raise Exception(result['error'])
            return
        time.sleep(1)
    raise Exception('Operation %s did not complete within %d', operation,
                    timeout_sec)


def wait_for_healthy_backends(gcp,
                              backend_service,
                              instance_group,
                              timeout_sec=WAIT_FOR_BACKEND_SEC):
    start_time = time.time()
    config = {'group': instance_group.url}
    while time.time() - start_time <= timeout_sec:
        result = gcp.compute.backendServices().getHealth(
            project=gcp.project,
            backendService=backend_service.name,
            body=config).execute()
        if 'healthStatus' in result:
            healthy = True
            for instance in result['healthStatus']:
                if instance['healthState'] != 'HEALTHY':
                    healthy = False
                    break
            if healthy:
                return
        time.sleep(1)
    raise Exception('Not all backends became healthy within %d seconds: %s' %
                    (timeout_sec, result))


def get_instance_names(gcp, instance_group):
    instance_names = []
    result = gcp.compute.instanceGroups().listInstances(
        project=gcp.project,
        zone=instance_group.zone,
        instanceGroup=instance_group.name,
        body={
            'instanceState': 'ALL'
        }).execute()
    for item in result['items']:
        # listInstances() returns the full URL of the instance, which ends with
        # the instance name. compute.instances().get() requires using the
        # instance name (not the full URL) to look up instance details, so we
        # just extract the name manually.
        instance_name = item['instance'].split('/')[-1]
        instance_names.append(instance_name)
    return instance_names


def start_xds_client(service_port):
    cmd = CLIENT_CMD.format(service_host=SERVICE_HOST,
                            service_port=service_port,
                            stats_port=STATS_PORT,
                            qps=QPS)
    bootstrap_path = None
    with tempfile.NamedTemporaryFile(delete=False) as bootstrap_file:
        bootstrap_file.write(
            BOOTSTRAP_TEMPLATE.format(
                node_id=socket.gethostname()).encode('utf-8'))
        bootstrap_path = bootstrap_file.name

    client_process = subprocess.Popen(shlex.split(cmd),
                                      env=dict(
                                          os.environ,
                                          GRPC_XDS_BOOTSTRAP=bootstrap_path))
    return client_process


class InstanceGroup(object):

    def __init__(self, name, url, zone):
        self.name = name
        self.url = url
        self.zone = zone


class GcpResource(object):

    def __init__(self, name, url):
        self.name = name
        self.url = url


class GcpState(object):

    def __init__(self, compute, project, suffix, tolerate_gcp_errors):
        self.compute = compute
        self.project = project
        self.suffix = suffix
        self.tolerate_gcp_errors = tolerate_gcp_errors
        self.health_check = None
        self.health_check_firewall_rule = None
        self.backend_services = []
        self.url_map = None
        self.target_http_proxy = None
        self.global_forwarding_rule = None
        self.service_port = None
        self.template_url = None
        self.instance_groups = []

    def clean_up(self):
        delete_global_forwarding_rule(self)
        delete_target_http_proxy(self)
        delete_url_map(self)
        delete_backend_services(self)
        delete_firewall(self)
        delete_health_check(self)
        delete_instance_groups(self)
        delete_instance_template(self)


# TODO: put in main()
if args.compute_discovery_document:
    with open(args.compute_discovery_document, 'r') as discovery_doc:
        compute = googleapiclient.discovery.build_from_document(
            discovery_doc.read())
else:
    compute = googleapiclient.discovery.build('compute', 'v1')

client_process = None

# PROJECT_ID = args.project_id
# ZONE = args.zone
# QPS = args.qps
# TEST_CASE = args.test_case
# CLIENT_CMD = args.client_cmd
# WAIT_FOR_BACKEND_SEC = args.wait_for_backend_sec
# TEMPLATE_NAME = 'test-template' + args.gcp_suffix
# INSTANCE_GROUP_NAME = 'test-ig' + args.gcp_suffix
# HEALTH_CHECK_NAME = 'test-hc' + args.gcp_suffix
# FIREWALL_RULE_NAME = 'test-fw-rule' + args.gcp_suffix
# BACKEND_SERVICE_NAME = 'test-backend-service' + args.gcp_suffix
# URL_MAP_NAME = 'test-map' + args.gcp_suffix
# SERVICE_HOST = 'grpc-test' + args.gcp_suffix
# TARGET_PROXY_NAME = 'test-target-proxy' + args.gcp_suffix
# FORWARDING_RULE_NAME = 'test-forwarding-rule' + args.gcp_suffix
# KEEP_GCP_RESOURCES = args.keep_gcp_resources
# TOLERATE_GCP_ERRORS = args.tolerate_gcp_errors
# STATS_PORT = args.stats_port
# INSTANCE_GROUP_SIZE = 2
# WAIT_FOR_OPERATION_SEC = 60
# NUM_TEST_RPCS = 10 * QPS
# WAIT_FOR_STATS_SEC = 30

try:
    gcp = GcpState(compute, args.project, args.gcp_suffix,
                   args.tolerate_gcp_errors)
    backend_service_url = None
    health_check_url = None
    instance_group_url = None
    service_port = None
    template_url = None
    try:
        create_health_check(gcp, _BASE_HEALTH_CHECK_NAME)
        create_health_check_firewall_rule(gcp, _BASE_FIREWALL_RULE_NAME)
        add_backend_service(gcp, _BASE_BACKEND_SERVICE_NAME)
        create_url_map(gcp, _BASE_URL_MAP_NAME, gcp.backend_services[0],
                       _BASE_SERVICE_HOST)
        create_target_http_proxy(gcp, _BASE_TARGET_PROXY_NAME)
        potential_service_ports = list(args.service_port_range)
        random.shuffle(potential_service_ports)
        for port in potential_service_ports:
            try:
                create_global_forwarding_rule(
                    gcp,
                    port,
                    _BASE_FORWARDING_RULE_NAME,
                )
                gcp.service_port = port
                break
            except googleapiclient.errors.HttpError as http_error:
                logger.warning(
                    'Got error %s when attempting to create forwarding rule to port %d. Retrying with another port.'
                    % (http_error, port))
        if not gcp.service_port:
            raise Exception('Failed to pick a service port in the range %s' %
                            args.service_port_range)
        create_instance_template(gcp, _BASE_TEMPLATE_NAME, args.network,
                                 args.source_image)
        add_instance_group(compute, args.zone, _BASE_INSTANCE_GROUP_NAME,
                           INSTANCE_GROUP_SIZE)
        add_instances_to_backend(gcp, BACKEND_SERVICE_NAME,
                                 [instance_group_url])
    except googleapiclient.errors.HttpError as http_error:
        # if TOLERATE_GCP_ERRORS: # TODO: move into add functions?
        #     logger.warning(
        #         'Failed to set up backends: %s. Continuing since '
        #         '--tolerate_gcp_errors=true', http_error)
        #     if not instance_group_url:
        #         result = compute.instanceGroups().get(
        #             project=PROJECT_ID,
        #             zone=ZONE,
        #             instanceGroup=INSTANCE_GROUP_NAME).execute()
        #         instance_group_url = result['selfLink']
        #     if not template_url:
        #         result = compute.instanceTemplates().get(
        #             project=PROJECT_ID,
        #             instanceTemplate=TEMPLATE_NAME).execute()
        #         template_url = result['selfLink']
        #     if not backend_service_url:
        #         result = compute.backendServices().get(
        #             project=PROJECT_ID,
        #             backendService=BACKEND_SERVICE_NAME).execute()
        #         backend_service_url = result['selfLink']
        #     if not health_check_url:
        #         result = compute.healthChecks().get(
        #             project=PROJECT_ID,
        #             healthCheck=HEALTH_CHECK_NAME).execute()
        #         health_check_url = result['selfLink']
        #     if not service_port:
        #         service_port = args.service_port_range[0]
        #         logger.warning('Using arbitrary service port in range: %d' %
        #                        service_port)
        # else:
        raise http_error

    wait_for_healthy_backends(gcp, gcp.backend_services[0],
                              gcp.instance_groups[0])

    client_process = start_xds_client(gcp.service_port)

    if TEST_CASE == 'all':
        test_ping_pong(instance_names, NUM_TEST_RPCS, WAIT_FOR_STATS_SEC)
        test_round_robin(instance_names, NUM_TEST_RPCS, WAIT_FOR_STATS_SEC)
        # TODO: add other test cases here
    elif TEST_CASE == 'backends_restart':
        test_backends_restart(compute, PROJECT_ID, ZONE, instance_names,
                              INSTANCE_GROUP_NAME, BACKEND_SERVICE_NAME,
                              instance_group_url, NUM_TEST_RPCS,
                              WAIT_FOR_STATS_SEC)
    elif TEST_CASE == 'change_backend_service':
        test_change_backend_service(compute, PROJECT_ID, ZONE,
                                    BACKEND_SERVICE_NAME, backend_service_url,
                                    URL_MAP_NAME, INSTANCE_GROUP_NAME,
                                    instance_group_url, instance_names,
                                    service_port, template_url,
                                    health_check_url, WAIT_FOR_STATS_SEC)
    elif TEST_CASE == 'new_instance_group_receives_traffic':
        test_new_instance_group_receives_traffic(
            compute, PROJECT_ID, ZONE, BACKEND_SERVICE_NAME,
            INSTANCE_GROUP_NAME, instance_group_url, instance_names,
            service_port, template_url, WAIT_FOR_STATS_SEC)
    elif TEST_CASE == 'ping_pong':
        test_ping_pong(gcp)
    elif TEST_CASE == 'remove_instance_group':
        test_remove_instance_group(compute, PROJECT_ID, ZONE,
                                   BACKEND_SERVICE_NAME, INSTANCE_GROUP_NAME,
                                   instance_group_url, instance_names,
                                   service_port, template_url,
                                   WAIT_FOR_STATS_SEC)
    elif TEST_CASE == 'round_robin':
        test_round_robin(instance_names, NUM_TEST_RPCS, WAIT_FOR_STATS_SEC)
    elif TEST_CASE == 'secondary_locality_gets_no_requests_on_partial_primary_failure':
        test_secondary_locality_gets_no_requests_on_partial_primary_failure(
            compute, PROJECT_ID, ZONE, BACKEND_SERVICE_NAME,
            INSTANCE_GROUP_NAME, instance_group_url, instance_names,
            service_port, template_url, WAIT_FOR_STATS_SEC)
    elif TEST_CASE == 'secondary_locality_gets_requests_on_primary_failure':
        test_secondary_locality_gets_requests_on_primary_failure(
            compute, PROJECT_ID, ZONE, BACKEND_SERVICE_NAME,
            INSTANCE_GROUP_NAME, instance_group_url, instance_names,
            service_port, template_url, WAIT_FOR_STATS_SEC)
    else:
        logger.error('Unknown test case: %s', TEST_CASE)
        sys.exit(1)
finally:
    if client_process:
        # TODO: raise exception if process already terminated
        client_process.terminate()
    if not KEEP_GCP_RESOURCES:
        logger.info('Cleaning up GCP resources. This may take some time.')
        gcp.clean_up()

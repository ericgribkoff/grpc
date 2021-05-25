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
import logging

from absl import flags
from absl.testing import absltest
from typing import List

from framework import xds_k8s_testcase

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)

# Type aliases
_XdsTestServer = xds_k8s_testcase.XdsTestServer
_XdsTestClient = xds_k8s_testcase.XdsTestClient


class BaselineTest(xds_k8s_testcase.RegularXdsKubernetesTestCase):

    def _basic_setup(self, negs=1, replica_count=2, backend_services=1):
        with self.subTest('00_create_health_check'):
            if xds_k8s_testcase.USE_EXISTING_RESOURCES.value:
                self.td.load_health_check()
            else:
                self.td.create_health_check()

        with self.subTest('01_create_backend_service'):
            if xds_k8s_testcase.USE_EXISTING_RESOURCES.value:
                self.td.load_backend_service()
                if backend_services == 2:  # TODO(ericgribkoff) Fix
                    self.td.load_backend_service(
                        name=self.td.ALTERNATE_BACKEND_SERVICE_NAME)
            else:
                self.td.create_backend_service()
                if backend_services == 2:  # TODO(ericgribkoff) Fix
                    self.td.create_backend_service(
                        name=self.td.ALTERNATE_BACKEND_SERVICE_NAME)

        with self.subTest('02_create_url_map'):
            if xds_k8s_testcase.USE_EXISTING_RESOURCES.value:
                self.td.load_url_map()
            else:
                self.td.create_url_map(self.server_xds_host,
                                       self.server_xds_port)

        with self.subTest('03_create_target_proxy'):
            if xds_k8s_testcase.USE_EXISTING_RESOURCES.value:
                self.td.load_target_proxy()
            else:
                self.td.create_target_proxy()

        with self.subTest('04_create_forwarding_rule'):
            if xds_k8s_testcase.USE_EXISTING_RESOURCES.value:
                self.td.load_forwarding_rule()
            else:
                self.td.create_forwarding_rule(self.server_xds_port)

        with self.subTest('05_start_test_server'):
            self._default_test_servers: List[_XdsTestServer] = self.startTestServer(
                replica_count=replica_count)
            if negs == 2:
                # TODO(ericgribkoff) Fix
                self._same_zone_test_servers: List[
                    _XdsTestServer] = self.startTestServer(
                    server_runner=self.server_runners['secondary'],
                    replica_count=replica_count)
            if backend_services == 2:
                self._same_zone_test_servers: list[
                    _XdsTestServer] = self.startTestServer(
                    server_runner=self.server_runners['secondary'],
                    replica_count=replica_count)

        with self.subTest('06_add_server_backends_to_backend_service'):
            self.setupServerBackends()
            if negs == 2:
                self.setupServerBackends(
                    server_runner=self.server_runners['secondary'])
            if backend_services == 2:
                self.setupServerBackends(
                    server_runner=self.server_runners['secondary'],
                    bs_name=self.td.ALTERNATE_BACKEND_SERVICE_NAME)

        with self.subTest('07_start_test_client'):
            # TODO(ericgribkoff) clean up list
            self._test_client: _XdsTestClient = self.startTestClient(
                self._default_test_servers[0])

        with self.subTest('08_test_client_xds_config_exists'):
            self.assertXdsConfigExists(self._test_client)

        with self.subTest('09_test_server_received_rpcs_from_test_client'):
            self.assertSuccessfulRpcs(self._test_client)

    @absltest.skip('skip')
    def test_traffic_director_round_robin(self):
        REPLICA_COUNT = 2
        NUM_RPCS = 100
        expected_rpcs_per_replica = NUM_RPCS / REPLICA_COUNT
        self._basic_setup(replica_count=REPLICA_COUNT)

        rpcs_by_peer = self.getClientRpcStats(self._test_client,
                                              NUM_RPCS).rpcs_by_peer
        total_requests_received = sum(rpcs_by_peer[x] for x in rpcs_by_peer)

        self.assertEqual(total_requests_received, NUM_RPCS,
                         'Wrong number of RPCS')
        self.assertEqual(len(rpcs_by_peer), REPLICA_COUNT,
                         'Not all replicas received RPCs')
        for peer in rpcs_by_peer:
            self.assertTrue(
                abs(rpcs_by_peer[peer] - expected_rpcs_per_replica) <= 1,
                f'Wrong number of RPCs for {peer}')

    @absltest.skip('skip')
    def test_traffic_director_failover(self):
        REPLICA_COUNT = 3
        NUM_RPCS = 300
        self._basic_setup(replica_count=REPLICA_COUNT)

        with self.subTest(
            '1_test_secondary_locality_receives_no_requests_on_partial_primary_failure'):
            self._default_test_servers[
                0].update_health_service_client.set_not_serving()
            self._default_test_servers[0].health_client.check_health()
            client_rpc_stats = self.getClientRpcStats(self._test_client,
                                                      NUM_RPCS)

        with self.subTest('2_test_gentle_failover'):
            self._default_test_servers[
                1].update_health_service_client.set_not_serving()
            self._default_test_servers[1].health_client.check_health()
            client_rpc_stats = self.getClientRpcStats(self._test_client,
                                                      NUM_RPCS)

        with self.subTest(
            '3_test_secondary_locality_receives_requests_on_primary_failure'):
            self._default_test_servers[
                2].update_health_service_client.set_not_serving()
            self._default_test_servers[2].health_client.check_health()
            client_rpc_stats = self.getClientRpcStats(self._test_client,
                                                      NUM_RPCS)

        with self.subTest('4_test_traffic_resumes_to_healthy_backends'):
            for i in range(REPLICA_COUNT):
                self._default_test_servers[
                    i].update_health_service_client.set_not_serving()
                self._default_test_servers[i].health_client.check_health()
            client_rpc_stats = self.getClientRpcStats(self._test_client,
                                                      NUM_RPCS)

    @absltest.skip('skip')
    def test_traffic_director_remove_neg(self):
        NUM_RPCS = 300
        self._basic_setup(negs=2, replica_count=1)
        self.getClientRpcStats(self._test_client, NUM_RPCS)

    def test_traffic_director_change_backend_service(self):
        ADDITIONAL_BACKEND_SERVICE = 'alternate-backend-service'
        NUM_RPCS = 300
        self._basic_setup(negs=1, replica_count=1, backend_services=2)
        self.td.patch_url_map(self.server_xds_host, self.server_xds_port,
                              self.td.ALTERNATE_BACKEND_SERVICE_NAME)
        self.getClientRpcStats(self._test_client, NUM_RPCS)


if __name__ == '__main__':
    absltest.main(failfast=True)

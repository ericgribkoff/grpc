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

from framework import xds_k8s_testcase

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)

# Type aliases
_XdsTestServer = xds_k8s_testcase.XdsTestServer
_XdsTestClient = xds_k8s_testcase.XdsTestClient


class BaselineTest(xds_k8s_testcase.RegularXdsKubernetesTestCase):

    def _basic_setup(self, negs=1, replica_count=2, backend_services=1):
        with self.subTest('0_create_health_check'):
            # TODO(ericgribkoff) Structure these appropriately (without subTests?)
            if xds_k8s_testcase.USE_EXISTING_RESOURCES.value:
                self.td.load_health_check()
            else:
                self.td.create_health_check()

        with self.subTest('1_create_backend_service'):
            if xds_k8s_testcase.USE_EXISTING_RESOURCES.value:
                self.td.load_backend_service()
                if backend_services == 2: # TODO(ericgribkoff) Fix
                    self.td.load_backend_service(name=self.td.ALTERNATE_BACKEND_SERVICE_NAME)
            else:
                self.td.create_backend_service()
                if backend_services == 2: # TODO(ericgribkoff) Fix
                    self.td.create_backend_service(name=self.td.ALTERNATE_BACKEND_SERVICE_NAME)

        with self.subTest('2_create_url_map'):
            if xds_k8s_testcase.USE_EXISTING_RESOURCES.value:
                self.td.load_url_map()
            else:
                self.td.create_url_map(self.server_xds_host, self.server_xds_port)

        with self.subTest('3_create_target_proxy'):
            if xds_k8s_testcase.USE_EXISTING_RESOURCES.value:
                self.td.load_target_proxy()
            else:
                self.td.create_target_proxy()

        with self.subTest('4_create_forwarding_rule'):
            if xds_k8s_testcase.USE_EXISTING_RESOURCES.value:
                self.td.load_forwarding_rule()
            else:
                self.td.create_forwarding_rule(self.server_xds_port)

        with self.subTest('5_start_test_server'):
            self._default_test_servers: list[_XdsTestServer] = self.startTestServer(replica_count=replica_count)
            if negs == 2:
                # TODO(ericgribkoff) Fix
                self._same_zone_test_servers: list[_XdsTestServer] = self.startTestServer(server_runner=self.server_runners['secondary'], replica_count=replica_count)
            if backend_services == 2:
                self._same_zone_test_servers: list[_XdsTestServer] = self.startTestServer(server_runner=self.server_runners['secondary'], replica_count=replica_count)
            # alternate_test_servers: list[_XdsTestServer] = self.startTestServer(replica_count=1, server_runner=self.server_runners['alternate'])

        with self.subTest('6_add_server_backends_to_backend_service'):
            self.setupServerBackends() #wait_for_healthy_status=False)
            if negs == 2:
                self.setupServerBackends(server_runner=self.server_runners['secondary'])
            if backend_services == 2:
                self.setupServerBackends(server_runner=self.server_runners['secondary'], bs_name=self.td.ALTERNATE_BACKEND_SERVICE_NAME)
            # self.setupServerBackends(server_runner=self.server_runners['alternate']) #wait_for_healthy_status=False)

        with self.subTest('7_start_test_client'):
            # TODO(ericgribkoff) clean up list
            self._test_client: _XdsTestClient = self.startTestClient(self._default_test_servers[0])

        with self.subTest('8_test_client_xds_config_exists'):
            self.assertXdsConfigExists(self._test_client)

        with self.subTest('9_test_server_received_rpcs_from_test_client'):
            self.assertSuccessfulRpcs(self._test_client)

    @absltest.skip('skip')
    def test_traffic_director_round_robin(self):
        self._basic_setup()

        with self.subTest('10_test_round_robin'):
            # for i in range(30):
            client_rpc_stats = self.getClientRpcStats(self._test_client, 100)
            requests_received = [client_rpc_stats.rpcs_by_peer[x] for x in client_rpc_stats.rpcs_by_peer]
            total_requests_received = sum(requests_received)
            self.assertEqual(total_requests_received, 100)
            expected_requests = total_requests_received / len(self._default_test_servers)
            self.assertTrue(all([abs(x - expected_requests) <= 1 for x in requests_received]))

    @absltest.skip('skip')
    def test_traffic_director_failover(self):
        self._basic_setup()

        with self.subTest('10_test_one_unhealthy_backend'):
            # for i in range(30):
            self.getClientRpcStats(self._test_client, 100)
            # requests_received = [client_rpc_stats.rpcs_by_peer[x] for x in client_rpc_stats.rpcs_by_peer]
            # total_requests_received = sum(requests_received)
            # self.assertEqual(total_requests_received, 100)
            # expected_requests = total_requests_received / len(default_test_servers)
            # self.assertTrue(all([abs(x - expected_requests) <= 1 for x in requests_received]))
            self._default_test_servers[0].update_health_service_client.set_not_serving()
            self._default_test_servers[0].health_client.check_health()
            for i in range(6):
                self.getClientRpcStats(self._test_client, 100)
            self._default_test_servers[1].update_health_service_client.set_not_serving()
            self._default_test_servers[1].health_client.check_health()
            for i in range(6):
                self.getClientRpcStats(self._test_client, 100)
            self._default_test_servers[2].update_health_service_client.set_not_serving()
            self._default_test_servers[2].health_client.check_health()
            for i in range(6):
                self.getClientRpcStats(self._test_client, 100)
            # TODO(ericgribkoff): combine into setNotServing/setServing that also logs check_health
            self._default_test_servers[0].update_health_service_client.set_not_serving()
            self._default_test_servers[1].update_health_service_client.set_not_serving()
            self._default_test_servers[2].update_health_service_client.set_not_serving()
            self._default_test_servers[0].health_client.check_health()
            self._default_test_servers[1].health_client.check_health()
            self._default_test_servers[2].health_client.check_health()
            for i in range(15):
                self.getClientRpcStats(self._test_client, 100)
        # with self.subTest('11_setup_secondary_locality'):
        #     self.setupServerBackends(server_runner=self.server_runners['alternate']) #wait_for_healthy_status=False)

    @absltest.skip('skip')
    def test_traffic_director_remove_neg(self):
        self._basic_setup(negs=2, replica_count=1)
        for i in range(5):
            self.getClientRpcStats(self._test_client, 100)
        # TODO(ericgribkoff) Remove neg and assert

    def test_traffic_director_change_backend_service(self):
        self._basic_setup(negs=1, replica_count=1, backend_services=2)
        for i in range(2):
            self.getClientRpcStats(self._test_client, 100)
        self.td.patch_url_map(self.server_xds_host, self.server_xds_port, self.td.ALTERNATE_BACKEND_SERVICE_NAME)
        for i in range(20):
            self.getClientRpcStats(self._test_client, 100)

if __name__ == '__main__':
    absltest.main(failfast=True)

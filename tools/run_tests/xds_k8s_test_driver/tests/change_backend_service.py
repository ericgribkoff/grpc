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
from typing import Optional

from absl import flags
from absl.testing import absltest
from typing import List

from typing import List

from framework import xds_k8s_testcase
from framework.infrastructure import k8s
from framework.test_app import server_app

logger = logging.getLogger(__name__)
flags.adopt_module_key_flags(xds_k8s_testcase)

# Type aliases
_XdsTestServer = xds_k8s_testcase.XdsTestServer
_XdsTestClient = xds_k8s_testcase.XdsTestClient


class ChangeBackendServiceTest(xds_k8s_testcase.RegularXdsKubernetesTestCase):
    ALTERNATE_BACKEND_SERVICE_NAME = 'alternate-backend-service'

    def test_change_backend_service(self) -> None:
        with self.subTest('00_create_health_check'):
            self.td.create_health_check()

        with self.subTest('01_create_backend_services'):
            self.td.create_backend_service()
            self.td.create_backend_service(
                name=self.ALTERNATE_BACKEND_SERVICE_NAME)

        with self.subTest('02_create_url_map'):
            self.td.create_url_map(self.server_xds_host,
                                   self.server_xds_port)

        with self.subTest('03_create_target_proxy'):
            self.td.create_target_proxy()

        with self.subTest('04_create_forwarding_rule'):
            self.td.create_forwarding_rule(self.server_xds_port)

        with self.subTest('05_start_test_servers'):
            self._default_test_servers: List[
                _XdsTestServer] = self.startTestServer(
                server_runner=self.server_runners['default'])
            self.server_runners[
                'secondary'] = server_app.KubernetesServerRunner(
                k8s.KubernetesNamespace(self.k8s_api_manager,
                                        self.server_namespace),
                deployment_name=self.server_name + '-same-zone',
                image_name=self.server_image,
                gcp_service_account=self.gcp_service_account,
                td_bootstrap_image=self.td_bootstrap_image,
                xds_server_uri=self.xds_server_uri,
                network=self.network,
                debug_use_port_forwarding=self.debug_use_port_forwarding,
                reuse_namespace=True)
            self._same_zone_test_servers: List[
                _XdsTestServer] = self.startTestServer(
                server_runner=self.server_runners['secondary'],
                replica_count=1)

        with self.subTest('06_add_server_backends_to_backend_services'):
            self.setupServerBackends()
            self.setupServerBackends(
                server_runner=self.server_runners['secondary'],
                bs_name=self.ALTERNATE_BACKEND_SERVICE_NAME)

        with self.subTest('07_start_test_client'):
            self._test_client: _XdsTestClient = self.startTestClient(
                self._default_test_servers[0])

        with self.subTest('08_test_client_xds_config_exists'):
            self.assertXdsConfigExists(self._test_client)

        with self.subTest('09_test_server_received_rpcs_from_test_client'):
            self.assertSuccessfulRpcs(self._test_client)

        with self.subTest('10_change_backend_service'):
            NUM_RPCS = 300
            self.td.patch_url_map(self.server_xds_host, self.server_xds_port,
                                  self.ALTERNATE_BACKEND_SERVICE_NAME)
            self.getClientRpcStats(self._test_client, NUM_RPCS)


if __name__ == '__main__':
    absltest.main(failfast=True)

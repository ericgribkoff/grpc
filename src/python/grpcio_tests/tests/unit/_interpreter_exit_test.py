# Copyright 2015 gRPC authors.
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
"""Tests to ensure that gRPC cleanup code does not fail on interpreter exit"""


import logging
import subprocess
import sys
import unittest

import grpc

INTERPRETER = sys.executable

class InterpreterExitTest(unittest.TestCase):

    def test_server_cleanup_exits_cleanly(self):
        script = """if True:
            import sys

            import grpc
            from tests.unit import test_common

            servers = []
            while len(servers) < 1000:
                server = test_common.test_server()
                server.add_insecure_port('[::]:0')
                server.start()
                servers.append(server)
            sys.exit(0)

        """
        process = subprocess.Popen(
            [INTERPRETER, '-c', script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        out, err = process.communicate()
        self.assertEquals(0, process.returncode, "process failed with exit code %d" % (process.returncode))

if __name__ == '__main__':
    logging.basicConfig()
    unittest.main(verbosity=2)

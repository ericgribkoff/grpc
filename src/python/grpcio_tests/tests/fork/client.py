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
"""The Python implementation of the GRPC interoperability test client."""

import argparse
import os

from google import auth as google_auth
from google.auth import jwt as google_auth_jwt
import grpc
from src.proto.grpc.testing import test_pb2_grpc

from tests.fork import methods
from tests.fork import resources


def _args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--server_host',
        default="localhost",
        type=str,
        help='the host to which to connect')
    parser.add_argument(
        '--server_port',
        type=int,
        required=True,
        help='the port to which to connect')
    parser.add_argument(
        '--test_case',
        default='large_unary',
        type=str,
        help='the test case to execute')
    parser.add_argument(
        '--use_tls',
        default=False,
        type=resources.parse_bool,
        help='require a secure connection')
    parser.add_argument(
        '--use_test_ca',
        default=False,
        type=resources.parse_bool,
        help='replace platform root CAs with ca.pem')
    parser.add_argument(
        '--server_host_override',
        default="foo.test.google.fr",
        type=str,
        help='the server host to which to claim to connect')
    parser.add_argument(
        '--oauth_scope', type=str, help='scope for OAuth tokens')
    parser.add_argument(
        '--default_service_account',
        type=str,
        help='email address of the default service account')
    return parser.parse_args()


def _test_case_from_arg(test_case_arg):
    for test_case in methods.TestCase:
        if test_case_arg == test_case.value:
            return test_case
    else:
        raise ValueError('No test case "%s"!' % test_case_arg)


def test_fork():
    args = _args()
    test_case = _test_case_from_arg(args.test_case)
    test_case.run_test(args)
    

if __name__ == '__main__':
    test_fork()

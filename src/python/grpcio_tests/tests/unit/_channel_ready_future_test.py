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
"""Tests of grpc.channel_ready_future."""

import os
import random
import sys
import threading
import time
import unittest
import logging

import grpc
from tests.unit.framework.common import test_constants
from tests.unit import _thread_pool
from tests.unit import test_common


class _Callback(object):

    def __init__(self):
        self._condition = threading.Condition()
        self._value = None

    def accept_value(self, value):
        with self._condition:
            self._value = value
            self._condition.notify_all()

    def block_until_called(self):
        with self._condition:
            while self._value is None:
                self._condition.wait()
            return self._value

thread_has_run = set()

def random_io():
    '''Loop for a while sleeping random tiny amounts and doing some I/O.'''
    while True:
        in_f = open(os.__file__, 'rb')
        stuff = in_f.read(200)
        null_f = open(os.devnull, 'wb')
        null_f.write(stuff)
        # time.sleep(random.random() / 1995)
        null_f.close()
        in_f.close()
        thread_has_run.add(threading.current_thread())


class ChannelReadyFutureTest(unittest.TestCase):

    # def setUp(self):
    #     server = test_common.test_server()
    #     self._port = server.add_insecure_port('[::]:0')
    #     server.start()

    def do_stuff(self):
        number_of_runs = 0
        while True:
            ports = []
            i = 0
            # for i in range(2):
            server = test_common.test_server()
            ports.append(server.add_insecure_port('[::]:0'))
            server.start()

            channel = grpc.insecure_channel('localhost:12345')
            callback = _Callback()

            ready_future = grpc.channel_ready_future(channel)
            ready_future.add_done_callback(callback.accept_value)
            with self.assertRaises(grpc.FutureTimeoutError):
                ready_future.result(timeout=test_constants.SHORT_TIMEOUT)
            self.assertFalse(ready_future.cancelled())
            self.assertFalse(ready_future.done())
            self.assertTrue(ready_future.running())
            ready_future.cancel()
            value_passed_to_callback = callback.block_until_called()
            self.assertIs(ready_future, value_passed_to_callback)
            self.assertTrue(ready_future.cancelled())
            self.assertTrue(ready_future.done())
            self.assertFalse(ready_future.running())

        # for i in range(2):
            # channel = grpc.insecure_channel('localhost:{}'.format(ports[i]))
            # callback = _Callback()

            # ready_future = grpc.channel_ready_future(channel)
            # ready_future.add_done_callback(callback.accept_value)
            # self.assertIsNone(
            #     ready_future.result(timeout=test_constants.LONG_TIMEOUT))
            # value_passed_to_callback = callback.block_until_called()
            # self.assertIs(ready_future, value_passed_to_callback)
            # self.assertFalse(ready_future.cancelled())
            # self.assertTrue(ready_future.done())
            # self.assertFalse(ready_future.running())
            # time.sleep(random.random() / 1995)
            # # Cancellation after maturity has no effect.
            # ready_future.cancel()
            # self.assertFalse(ready_future.cancelled())
            # self.assertTrue(ready_future.done())
            # self.assertFalse(ready_future.running())

            number_of_runs += 1
            if number_of_runs > 10:
                thread_has_run.add(threading.current_thread())

    def test_daemon_crash(self):
        count = 0
        for _ in range(100):
            new_thread = threading.Thread(target=self.do_stuff)
            new_thread.daemon = True
            new_thread.start()
            count += 1
        # time.sleep(0.1)
        while len(thread_has_run) < count*2:
            time.sleep(0.001)
        # time.sleep(1)
        # Trigger process shutdown
        sys.exit(0)

    # def test_lonely_channel_connectivity(self):
    #     channel = grpc.insecure_channel('localhost:12345')
    #     callback = _Callback()

    #     ready_future = grpc.channel_ready_future(channel)
    #     ready_future.add_done_callback(callback.accept_value)
    #     with self.assertRaises(grpc.FutureTimeoutError):
    #         ready_future.result(timeout=test_constants.SHORT_TIMEOUT)
    #     self.assertFalse(ready_future.cancelled())
    #     self.assertFalse(ready_future.done())
    #     self.assertTrue(ready_future.running())
    #     ready_future.cancel()
    #     value_passed_to_callback = callback.block_until_called()
    #     self.assertIs(ready_future, value_passed_to_callback)
    #     self.assertTrue(ready_future.cancelled())
    #     self.assertTrue(ready_future.done())
    #     self.assertFalse(ready_future.running())

    # def test_immediately_connectable_channel_connectivity(self):
    #     thread_pool = _thread_pool.RecordingThreadPool(max_workers=None)
    #     server = grpc.server(thread_pool, options=(('grpc.so_reuseport', 0),))
    #     port = server.add_insecure_port('[::]:0')
    #     server.start()
    #     channel = grpc.insecure_channel('localhost:{}'.format(port))
    #     callback = _Callback()

    #     ready_future = grpc.channel_ready_future(channel)
    #     ready_future.add_done_callback(callback.accept_value)
    #     self.assertIsNone(
    #         ready_future.result(timeout=test_constants.LONG_TIMEOUT))
    #     value_passed_to_callback = callback.block_until_called()
    #     self.assertIs(ready_future, value_passed_to_callback)
    #     self.assertFalse(ready_future.cancelled())
    #     self.assertTrue(ready_future.done())
    #     self.assertFalse(ready_future.running())
    #     # Cancellation after maturity has no effect.
    #     ready_future.cancel()
    #     self.assertFalse(ready_future.cancelled())
    #     self.assertTrue(ready_future.done())
    #     self.assertFalse(ready_future.running())
    #     self.assertFalse(thread_pool.was_used())


if __name__ == '__main__':
    logging.basicConfig()
    unittest.main(verbosity=2)

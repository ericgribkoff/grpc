# Copyright 2018 gRPC authors.
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
"""Implementations of fork support test methods."""

import enum
import json
import logging
import multiprocessing
import os
import threading
import time

import grpc

from six.moves import queue

from src.proto.grpc.testing import empty_pb2
from src.proto.grpc.testing import messages_pb2
from src.proto.grpc.testing import test_pb2_grpc

_LOGGER = logging.getLogger(__name__)


def _channel(args):
    target = '{}:{}'.format(args.server_host, args.server_port)
    if args.use_tls:
        channel_credentials = grpc.ssl_channel_credentials()
        channel = grpc.secure_channel(target, channel_credentials)
    else:
        channel = grpc.insecure_channel(target)
    return channel


def _validate_payload_type_and_length(response, expected_type, expected_length):
    if response.payload.type is not expected_type:
        raise ValueError('expected payload type %s, got %s' %
                         (expected_type, type(response.payload.type)))
    elif len(response.payload.body) != expected_length:
        raise ValueError('expected payload body size %d, got %d' %
                         (expected_length, len(response.payload.body)))


def _async_unary(stub):
    size = 314159
    request = messages_pb2.SimpleRequest(
        response_type=messages_pb2.COMPRESSABLE,
        response_size=size,
        payload=messages_pb2.Payload(body=b'\x00' * 271828))
    print('(%d) about to create future' % os.getpid())
    response_future = stub.UnaryCall.future(request)
    print('(%d) created future' % os.getpid())
    response = response_future.result()
    print('(%d) got future result' % os.getpid())
    _validate_payload_type_and_length(response, messages_pb2.COMPRESSABLE, size)


def _blocking_unary(stub):
    size = 314159
    request = messages_pb2.SimpleRequest(
        response_type=messages_pb2.COMPRESSABLE,
        response_size=size,
        payload=messages_pb2.Payload(body=b'\x00' * 271828))
    print("(%d) sending blocking unary" % os.getpid())
    response = stub.UnaryCall(request)
    print("(%d) got blocking unary result" % os.getpid())
    _validate_payload_type_and_length(response, messages_pb2.COMPRESSABLE, size)


class _Pipe(object):

    def __init__(self):
        self._condition = threading.Condition()
        self._values = []
        self._open = True

    def __iter__(self):
        return self

    def __next__(self):
        return self.next()

    def next(self):
        with self._condition:
            while not self._values and self._open:
                self._condition.wait()
            if self._values:
                return self._values.pop(0)
            else:
                raise StopIteration()

    def add(self, value):
        with self._condition:
            self._values.append(value)
            self._condition.notify()

    def close(self):
        with self._condition:
            self._open = False
            self._condition.notify()

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.close()


class _ChildProcess(object):

    def __init__(self, task, args=None):
        if args is None:
            args = ()
        self._exceptions = multiprocessing.Queue()

        def record_exceptions():
            print("in record_exceptions for %d" % os.getpid())
            _LOGGER.info("\033[94m child pid: %d \033[0m", os.getpid())
            try:
                task(*args)
            except Exception as e:  # pylint: disable=broad-except
                print "exception!"
                self._exceptions.put(e)

        self._process = multiprocessing.Process(target=record_exceptions)

    def start(self):
        self._process.start()

    def finish(self):
        print('attempting to join')
        print('_process.is_alive()', self._process.is_alive())
        print('_process.exitcode', self._process.exitcode)
        self._process.join()
        try:
            exception = self._exceptions.get(block=False)
            raise ValueError('Child process failed: %s' % exception)
        except queue.Empty:
            pass


def _async_unary_same_channel(channel):

    def child_target():
        _async_unary(stub)

    stub = test_pb2_grpc.TestServiceStub(channel)
    _async_unary(stub)
    child_process = _ChildProcess(child_target)
    child_process.start()
    _async_unary(stub)
    child_process.finish()


def _async_unary_new_channel(channel, args):

    def child_target():
        child_channel = _channel(args)
        child_stub = test_pb2_grpc.TestServiceStub(channel)
        _async_unary(child_stub)
        child_channel.close()

    stub = test_pb2_grpc.TestServiceStub(channel)
    _async_unary(stub)
    child_process = _ChildProcess(child_target)
    child_process.start()
    _async_unary(stub)
    child_process.finish()


def _blocking_unary_same_channel(channel):

    def child_target():
        _blocking_unary(stub)

    stub = test_pb2_grpc.TestServiceStub(channel)
    _blocking_unary(stub)
    child_process = _ChildProcess(child_target)
    child_process.start()
    _blocking_unary(stub)
    child_process.finish()


def _blocking_unary_new_channel(channel, args):

    def child_target():
        child_channel = _channel(args)
        child_stub = test_pb2_grpc.TestServiceStub(channel)
        _blocking_unary(child_stub)
        child_channel.close()

    stub = test_pb2_grpc.TestServiceStub(channel)
    _blocking_unary(stub)
    child_process = _ChildProcess(child_target)
    child_process.start()
    _blocking_unary(stub)
    child_process.finish()


# After a fork with a subscribed connectivity watcher:
#   Child process does not receive updates for the parent's subscribed callback
#   Child may subscribe a new connectivity watcher to the channel
def _connectivity_watch(channel):

    def child_target():
        # TODO: this should just use an event (on READY) and ignore other updates, no need for Queue
        # event = threading.Event()

        def child_connectivity_callback(state):
            # print('(child) state: ', state)
            child_states.append(state)
            # event.set()

        # print('polling before subscribe: ', channel._connectivity_state.polling)

        child_states = []
        channel.subscribe(child_connectivity_callback)
        _async_unary(stub)
        slept = False
        # if len(child_states
        #       ) < 2 or child_states[-1] != grpc.ChannelConnectivity.READY:
        #     # print('not ready, sleeping')
        #     slept = True
        #     time.sleep(2)
        #     if len(child_states
        #           ) < 2 or child_states[-1] != grpc.ChannelConnectivity.READY:
        #         # print('still not ready, sleeping again')
        #         time.sleep(2)
        # print('polling: ', channel._connectivity_state.polling)
        # print('waiting for event')
        # event.wait()
        # print('done waiting')
        if len(child_states
              ) < 2 or child_states[-1] != grpc.ChannelConnectivity.READY:
            raise ValueError('Channel did not move to READY')
        # if slept:
        #     raise ValueError('had to sleep to get test to pass!')
        if len(parent_states) > 1:
            raise ValueError('Received connectivity updates on parent callback')
        channel.unsubscribe(child_connectivity_callback)
        channel.close()

    def parent_connectivity_callback(state):
        # print('(parent) state: ', state)
        parent_states.append(state)

    parent_states = []
    channel.subscribe(parent_connectivity_callback)
    stub = test_pb2_grpc.TestServiceStub(channel)
    child_process = _ChildProcess(child_target)
    child_process.start()
    _async_unary(stub)

    # possible we can unsubscribe before the poll connectivity thread receives
    # the connectivity updates?

    # Need to unsubscribe or _channel.py in _poll_connectivity triggers a
    # "Cannot invoke RPC on closed channel!" error.
    # TODO(ericgribkoff) Fix issue with channel.close() and connectivity polling
    channel.unsubscribe(parent_connectivity_callback)

    if len(parent_states
          ) < 2 or parent_states[-1] != grpc.ChannelConnectivity.READY:
        raise ValueError('Channel did not move to READY')
    child_process.finish()


def _ping_pong_with_child_processes_after_first_response(
        channel, args, child_target, run_after_close=True):
    request_response_sizes = (
        31415,
        9,
        2653,
        58979,
    )
    request_payload_sizes = (
        27182,
        8,
        1828,
        45904,
    )
    stub = test_pb2_grpc.TestServiceStub(channel)
    pipe = _Pipe()
    parent_bidi_call = stub.FullDuplexCall(pipe)
    child_processes = []
    first_message_received = False
    for response_size, payload_size in zip(request_response_sizes,
                                           request_payload_sizes):
        request = messages_pb2.StreamingOutputCallRequest(
            response_type=messages_pb2.COMPRESSABLE,
            response_parameters=(
                messages_pb2.ResponseParameters(size=response_size),),
            payload=messages_pb2.Payload(body=b'\x00' * payload_size))
        pipe.add(request)
        if first_message_received:
            child_process = _ChildProcess(child_target,
                                          (parent_bidi_call, channel, args))
            child_process.start()
            child_processes.append(child_process)
        response = next(parent_bidi_call)
        first_message_received = True
        child_process = _ChildProcess(child_target,
                                      (parent_bidi_call, channel, args))
        child_process.start()
        child_processes.append(child_process)
        _validate_payload_type_and_length(response, messages_pb2.COMPRESSABLE,
                                          response_size)
    pipe.close()
    if run_after_close:
        child_process = _ChildProcess(child_target,
                                      (parent_bidi_call, channel, args))
        child_process.start()
        child_processes.append(child_process)
    for child_process in child_processes:
        print('parent calling finish() on child pid: ', child_process._process.pid)
        child_process.finish()
    print('parent done')


def _in_progress_bidi_continue_call(channel):

    def child_target(parent_bidi_call, parent_channel, args):
        stub = test_pb2_grpc.TestServiceStub(parent_channel)
        # The parent call may have finished before forking
        print('(%d) checking if call done' % os.getpid())
        parent_call_already_done = parent_bidi_call.done()
        if not parent_call_already_done:
            print('(%d) call not done' % os.getpid())
            try:
                parent_bidi_call.result(timeout=1)
                print('(%d) result finished' % os.getpid())
                raise ValueError('Received result on inherited call')
            except grpc.FutureTimeoutError:
                pass
        _async_unary(stub)
        if not parent_call_already_done:
            print('(%d) getting code' % os.getpid())
            inherited_code = parent_bidi_call.code()
            print('(%d) got code' % os.getpid())
            inherited_details = parent_bidi_call.details()
            if inherited_code != grpc.StatusCode.UNKNOWN:
                raise ValueError(
                    'Expected inherited code UNKNOWN, got %s' % inherited_code)
            if inherited_details != 'Stream removed':
                raise ValueError(
                    'Expected inherited details Stream removed, got %s' %
                    inherited_details)

    # Don't run child_target after closing the parent call, as the call may have received a status from the
    # server before fork occurs.
    _ping_pong_with_child_processes_after_first_response(
        channel, None, child_target, run_after_close=False)


def _in_progress_bidi_same_channel_async_call(channel):

    def child_target(parent_bidi_call, parent_channel, args):
        stub = test_pb2_grpc.TestServiceStub(parent_channel)
        _async_unary(stub)

    _ping_pong_with_child_processes_after_first_response(
        channel, None, child_target)


def _in_progress_bidi_same_channel_blocking_call(channel):

    def child_target(parent_bidi_call, parent_channel, args):
        stub = test_pb2_grpc.TestServiceStub(parent_channel)
        _blocking_unary(stub)

    _ping_pong_with_child_processes_after_first_response(
        channel, None, child_target)


def _in_progress_bidi_new_channel_async_call(channel, args):

    def child_target(parent_bidi_call, parent_channel, args):
        channel = _channel(args)
        stub = test_pb2_grpc.TestServiceStub(channel)
        _async_unary(stub)

    _ping_pong_with_child_processes_after_first_response(
        channel, args, child_target)


def _in_progress_bidi_new_channel_blocking_call(channel, args):

    def child_target(parent_bidi_call, parent_channel, args):
        channel = _channel(args)
        stub = test_pb2_grpc.TestServiceStub(channel)
        _blocking_unary(stub)

    _ping_pong_with_child_processes_after_first_response(
        channel, args, child_target)


@enum.unique
class TestCase(enum.Enum):

    CONNECTIVITY_WATCH = 'connectivity_watch'
    ASYNC_UNARY_SAME_CHANNEL = 'async_unary_same_channel'
    ASYNC_UNARY_NEW_CHANNEL = 'async_unary_new_channel'
    BLOCKING_UNARY_SAME_CHANNEL = 'blocking_unary_same_channel'
    BLOCKING_UNARY_NEW_CHANNEL = 'blocking_unary_new_channel'
    IN_PROGRESS_BIDI_CONTINUE_CALL = 'in_progress_bidi_continue_call'
    IN_PROGRESS_BIDI_SAME_CHANNEL_ASYNC_CALL = 'in_progress_bidi_same_channel_async_call'
    IN_PROGRESS_BIDI_SAME_CHANNEL_BLOCKING_CALL = 'in_progress_bidi_same_channel_blocking_call'
    IN_PROGRESS_BIDI_NEW_CHANNEL_ASYNC_CALL = 'in_progress_bidi_new_channel_async_call'
    IN_PROGRESS_BIDI_NEW_CHANNEL_BLOCKING_CALL = 'in_progress_bidi_new_channel_blocking_call'

    def run_test(self, args):
        _LOGGER.info("\033[94m Running %s \033[0m ", self)
        _LOGGER.info("\033[94m parent pid: %d \033[0m", os.getpid())
        channel = _channel(args)
        if self is TestCase.ASYNC_UNARY_SAME_CHANNEL:
            _async_unary_same_channel(channel)
        elif self is TestCase.ASYNC_UNARY_NEW_CHANNEL:
            _async_unary_new_channel(channel, args)
        elif self is TestCase.BLOCKING_UNARY_SAME_CHANNEL:
            _blocking_unary_same_channel(channel)
        elif self is TestCase.BLOCKING_UNARY_NEW_CHANNEL:
            _blocking_unary_new_channel(channel, args)
        elif self is TestCase.CONNECTIVITY_WATCH:
            _connectivity_watch(channel)
        elif self is TestCase.IN_PROGRESS_BIDI_CONTINUE_CALL:
            _in_progress_bidi_continue_call(channel)
        elif self is TestCase.IN_PROGRESS_BIDI_SAME_CHANNEL_ASYNC_CALL:
            _in_progress_bidi_same_channel_async_call(channel)
        elif self is TestCase.IN_PROGRESS_BIDI_SAME_CHANNEL_BLOCKING_CALL:
            _in_progress_bidi_same_channel_blocking_call(channel)
        elif self is TestCase.IN_PROGRESS_BIDI_NEW_CHANNEL_ASYNC_CALL:
            _in_progress_bidi_new_channel_async_call(channel, args)
        elif self is TestCase.IN_PROGRESS_BIDI_NEW_CHANNEL_BLOCKING_CALL:
            _in_progress_bidi_new_channel_blocking_call(channel, args)
        else:
            raise NotImplementedError(
                'Test case "%s" not implemented!' % self.name)
        channel.close()

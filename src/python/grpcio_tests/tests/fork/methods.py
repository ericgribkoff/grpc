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
"""Implementations of interoperability test methods."""

import enum
import json
import multiprocessing
import os
import threading
import time

from google import auth as google_auth
from google.auth import environment_vars as google_auth_environment_vars
from google.auth.transport import grpc as google_auth_transport_grpc
from google.auth.transport import requests as google_auth_transport_requests
import grpc
from grpc.beta import implementations
from six.moves import queue

from tests.fork import resources

from src.proto.grpc.testing import empty_pb2
from src.proto.grpc.testing import messages_pb2
from src.proto.grpc.testing import test_pb2_grpc


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
    response_future = stub.UnaryCall.future(request)
    response = response_future.result()
    _validate_payload_type_and_length(response, messages_pb2.COMPRESSABLE, size)
    return response


def _empty_unary(stub):
    response = stub.EmptyCall(empty_pb2.Empty())
    if not isinstance(response, empty_pb2.Empty):
        raise TypeError(
            'response is of type "%s", not empty_pb2.Empty!' % type(response))


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

    def __init__(self, task):
        self._exceptions = multiprocessing.Queue()
        def record_exceptions():
            try:
                task()
            except Exception as e:
                self._exceptions.put(str(e))
        self._process = multiprocessing.Process(target=record_exceptions)

    def start(self):
        self._process.start()

    def finish(self):
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
        _empty_unary(stub)

    stub = test_pb2_grpc.TestServiceStub(channel)
    _empty_unary(stub)
    child_process = _ChildProcess(child_target)
    child_process.start()
    _empty_unary(stub)
    child_process.finish()


def _blocking_unary_new_channel(channel, args):
    def child_target():
        child_channel = _channel(args)
        child_stub = test_pb2_grpc.TestServiceStub(channel)
        _empty_unary(child_stub)
        child_channel.close()

    stub = test_pb2_grpc.TestServiceStub(channel)
    _empty_unary(stub)
    child_process = _ChildProcess(child_target)
    child_process.start()
    _empty_unary(stub)
    child_process.finish()


# After a fork with a subscribed connectivity watcher:
#   Child process does not receive updates for the parent's subscribed callback
#   Child may subscribe a new connectivity watcher to the channel
def _connectivity_watch(channel):
    def child_target():
        def child_connectivity_callback(state):
                child_states.append(state)
        child_states = []
        channel.subscribe(child_connectivity_callback)
        _async_unary(stub)
        if len(child_states) < 2 or child_states[-1] != grpc.ChannelConnectivity.READY:
            raise ValueError('Channel did not move to READY')
        if len(parent_states) > 1:
            raise ValueError('Received connectivity updates on parent callback')

    def parent_connectivity_callback(state):
        parent_states.append(state)

    parent_states = []
    channel.subscribe(parent_connectivity_callback)
    stub = test_pb2_grpc.TestServiceStub(channel)
    child_process = _ChildProcess(child_target)
    child_process.start()
    _async_unary(stub)
    if len(parent_states) < 2 or parent_states[-1] != grpc.ChannelConnectivity.READY:
        raise ValueError('Channel did not move to READY')
    child_process.finish()


def _in_progress_bidi_continue_call(channel):
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

    def child_target():
        try:
            parent_bidi_call.result(timeout=1)
            raise ValueError('Received result on inherited call')
        except grpc.FutureTimeoutError:
            pass
        request = messages_pb2.StreamingOutputCallRequest(
            response_type=messages_pb2.COMPRESSABLE,
            response_parameters=(
                messages_pb2.ResponseParameters(size=1),),
            payload=messages_pb2.Payload(body=b'\x00' * 1))
        _async_unary(stub)
        inherited_code = parent_bidi_call.code()
        inherited_details = parent_bidi_call.details()
        if inherited_code != grpc.StatusCode.UNKNOWN:
            raise ValueError('Expected inherited code UNKNOWN, got %s' % inherited_code)
        if inherited_details != 'Stream removed':
            raise ValueError('Expected inherited details Stream removed, got %s' % inherited_details)

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
            child_process = _ChildProcess(child_target)
            child_process.start()
            child_processes.append(child_process)           
        response = next(parent_bidi_call)
        first_message_received = True
        child_process = _ChildProcess(child_target)
        child_process.start()
        child_processes.append(child_process)
        _validate_payload_type_and_length(
            response, messages_pb2.COMPRESSABLE, response_size)
    pipe.close()
    child_process = _ChildProcess(child_target)
    child_process.start()
    child_processes.append(child_process)
    for child_process in child_processes:
        child_process.finish()


def _in_progress_bidi_new_blocking_unary(channel):
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

    with _Pipe() as pipe:
        stub = test_pb2_grpc.TestServiceStub(channel)
        response_iterator = stub.FullDuplexCall(pipe)
        i = 0
        for response_size, payload_size in zip(request_response_sizes,
                                               request_payload_sizes):
            request = messages_pb2.StreamingOutputCallRequest(
                response_type=messages_pb2.COMPRESSABLE,
                response_parameters=(
                    messages_pb2.ResponseParameters(size=response_size),),
                payload=messages_pb2.Payload(body=b'\x00' * payload_size))
            pipe.add(request)
            if i == 2:
                # fork
                def child_process(child_error_queue):
                    try:
                        print('child process')
                        #pass
                        #response = next(response_iterator)
                        # child_states = []
                        # def child_callback(state):
                        #         child_states.append(state)
                        # channel.subscribe(child_callback)
                        time.sleep(2)
                        #_large_unary_common_behavior(stub, False, False, None)
                        #print('large unary sent')

                        response = stub.EmptyCall(empty_pb2.Empty())
                        print('got response: ', response)
                        # if len(child_states) < 2 or child_states[-1] != grpc.ChannelConnectivity.READY:
                        #     raise ValueError('Channel did not move to READY')
                        # if len(parent_states) > 1:
                        #     raise ValueError('Received connectivity updates on parent callback')
                    except Exception as e:
                        child_error_queue.put(str(e))
                child_error_queue = multiprocessing.Queue()
                process = multiprocessing.Process(target=child_process, args=(child_error_queue,))
                print('forking')
                process.start()
            response = next(response_iterator)
            _validate_payload_type_and_length(
                response, messages_pb2.COMPRESSABLE, response_size)
            i += 1

                # print('waiting to join')
                # process.join()
        pipe.close()
        print('main process done')
        print('\n\n\n\n')
        print(response_iterator.code())
        print('waiting to join')
        process.join()

def _channel(args):
    target = '{}:{}'.format(args.server_host, args.server_port)
    if args.use_tls:
        if args.use_test_ca:
            root_certificates = resources.test_root_certificates()
        else:
            root_certificates = None  # will load default roots.
        channel_credentials = grpc.ssl_channel_credentials(root_certificates)
        channel = grpc.secure_channel(target, channel_credentials, 
            #((
            #'grpc.ssl_target_name_override',
            #args.server_host_override,
           #),
           None)
    else:
        channel = grpc.insecure_channel(target)
    return channel


@enum.unique
class TestCase(enum.Enum):    
    # TODO: get errors from child callbacks without this
    import logging
    import sys
    logging.getLogger('grpc').addHandler(logging.StreamHandler(sys.stdout))

    ASYNC_UNARY_SAME_CHANNEL = 'async_unary_same_channel'
    ASYNC_UNARY_NEW_CHANNEL = 'async_unary_new_channel'
    BLOCKING_UNARY_SAME_CHANNEL = 'blocking_unary_same_channel'
    BLOCKING_UNARY_NEW_CHANNEL = 'blocking_unary_new_channel'
    CONNECTIVITY_WATCH = 'connectivity_watch'
    IN_PROGRESS_BIDI_CONTINUE_CALL = 'in_progress_bidi_continue_call'

    def run_test(self, args):
        channel = _channel(args)
        print self
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
        else:
            raise NotImplementedError(
                'Test case "%s" not implemented!' % self.name)
        channel.close()

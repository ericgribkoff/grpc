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

from src.proto.grpc.testing import empty_pb2
from src.proto.grpc.testing import messages_pb2
from src.proto.grpc.testing import test_pb2_grpc

_INITIAL_METADATA_KEY = "x-grpc-test-echo-initial"
_TRAILING_METADATA_KEY = "x-grpc-test-echo-trailing-bin"


def _maybe_echo_metadata(servicer_context):
    """Copies metadata from request to response if it is present."""
    invocation_metadata = dict(servicer_context.invocation_metadata())
    if _INITIAL_METADATA_KEY in invocation_metadata:
        initial_metadatum = (_INITIAL_METADATA_KEY,
                             invocation_metadata[_INITIAL_METADATA_KEY])
        servicer_context.send_initial_metadata((initial_metadatum,))
    if _TRAILING_METADATA_KEY in invocation_metadata:
        trailing_metadatum = (_TRAILING_METADATA_KEY,
                              invocation_metadata[_TRAILING_METADATA_KEY])
        servicer_context.set_trailing_metadata((trailing_metadatum,))


def _maybe_echo_status_and_message(request, servicer_context):
    """Sets the response context code and details if the request asks for them"""
    if request.HasField('response_status'):
        servicer_context.set_code(request.response_status.code)
        servicer_context.set_details(request.response_status.message)


def _expect_status_code(call, expected_code):
    if call.code() != expected_code:
        raise ValueError('expected code %s, got %s' % (expected_code,
                                                       call.code()))


def _expect_status_details(call, expected_details):
    if call.details() != expected_details:
        raise ValueError('expected message %s, got %s' % (expected_details,
                                                          call.details()))


def _validate_status_code_and_details(call, expected_code, expected_details):
    _expect_status_code(call, expected_code)
    _expect_status_details(call, expected_details)


def _validate_payload_type_and_length(response, expected_type, expected_length):
    if response.payload.type is not expected_type:
        raise ValueError('expected payload type %s, got %s' %
                         (expected_type, type(response.payload.type)))
    elif len(response.payload.body) != expected_length:
        raise ValueError('expected payload body size %d, got %d' %
                         (expected_length, len(response.payload.body)))


def _large_unary_common_behavior(stub, fill_username, fill_oauth_scope,
                                 call_credentials):
    size = 314159
    request = messages_pb2.SimpleRequest(
        response_type=messages_pb2.COMPRESSABLE,
        response_size=size,
        payload=messages_pb2.Payload(body=b'\x00' * 271828),
        fill_username=fill_username,
        fill_oauth_scope=fill_oauth_scope)
    response_future = stub.UnaryCall.future(
        request, credentials=call_credentials)
    response = response_future.result()
    _validate_payload_type_and_length(response, messages_pb2.COMPRESSABLE, size)
    return response


def _empty_unary(stub):
    response = stub.EmptyCall(empty_pb2.Empty())
    if not isinstance(response, empty_pb2.Empty):
        raise TypeError(
            'response is of type "%s", not empty_pb2.Empty!' % type(response))


def _large_unary(stub):
    _large_unary_common_behavior(stub, False, False, None)


def _client_streaming(stub):
    payload_body_sizes = (
        27182,
        8,
        1828,
        45904,
    )
    payloads = (messages_pb2.Payload(body=b'\x00' * size)
                for size in payload_body_sizes)
    requests = (messages_pb2.StreamingInputCallRequest(payload=payload)
                for payload in payloads)
    response = stub.StreamingInputCall(requests)
    if response.aggregated_payload_size != 74922:
        raise ValueError(
            'incorrect size %d!' % response.aggregated_payload_size)


def _server_streaming(stub):
    sizes = (
        31415,
        9,
        2653,
        58979,
    )

    request = messages_pb2.StreamingOutputCallRequest(
        response_type=messages_pb2.COMPRESSABLE,
        response_parameters=(
            messages_pb2.ResponseParameters(size=sizes[0]),
            messages_pb2.ResponseParameters(size=sizes[1]),
            messages_pb2.ResponseParameters(size=sizes[2]),
            messages_pb2.ResponseParameters(size=sizes[3]),
        ))
    response_iterator = stub.StreamingOutputCall(request)
    for index, response in enumerate(response_iterator):
        _validate_payload_type_and_length(response, messages_pb2.COMPRESSABLE,
                                          sizes[index])


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


def _ping_pong(stub):
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
        response_iterator = stub.FullDuplexCall(pipe)
        for response_size, payload_size in zip(request_response_sizes,
                                               request_payload_sizes):
            request = messages_pb2.StreamingOutputCallRequest(
                response_type=messages_pb2.COMPRESSABLE,
                response_parameters=(
                    messages_pb2.ResponseParameters(size=response_size),),
                payload=messages_pb2.Payload(body=b'\x00' * payload_size))
            pipe.add(request)
            response = next(response_iterator)
            _validate_payload_type_and_length(
                response, messages_pb2.COMPRESSABLE, response_size)


def _cancel_after_begin(stub):
    with _Pipe() as pipe:
        response_future = stub.StreamingInputCall.future(pipe)
        response_future.cancel()
        if not response_future.cancelled():
            raise ValueError('expected cancelled method to return True')
        if response_future.code() is not grpc.StatusCode.CANCELLED:
            raise ValueError('expected status code CANCELLED')


def _cancel_after_first_response(stub):
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
        response_iterator = stub.FullDuplexCall(pipe)

        response_size = request_response_sizes[0]
        payload_size = request_payload_sizes[0]
        request = messages_pb2.StreamingOutputCallRequest(
            response_type=messages_pb2.COMPRESSABLE,
            response_parameters=(
                messages_pb2.ResponseParameters(size=response_size),),
            payload=messages_pb2.Payload(body=b'\x00' * payload_size))
        pipe.add(request)
        response = next(response_iterator)
        # We test the contents of `response` in the Ping Pong test - don't check
        # them here.
        response_iterator.cancel()

        try:
            next(response_iterator)
        except grpc.RpcError as rpc_error:
            if rpc_error.code() is not grpc.StatusCode.CANCELLED:
                raise
        else:
            raise ValueError('expected call to be cancelled')


def _timeout_on_sleeping_server(stub):
    request_payload_size = 27182
    with _Pipe() as pipe:
        response_iterator = stub.FullDuplexCall(pipe, timeout=0.001)

        request = messages_pb2.StreamingOutputCallRequest(
            response_type=messages_pb2.COMPRESSABLE,
            payload=messages_pb2.Payload(body=b'\x00' * request_payload_size))
        pipe.add(request)
        try:
            next(response_iterator)
        except grpc.RpcError as rpc_error:
            if rpc_error.code() is not grpc.StatusCode.DEADLINE_EXCEEDED:
                raise
        else:
            raise ValueError('expected call to exceed deadline')


def _empty_stream(stub):
    with _Pipe() as pipe:
        response_iterator = stub.FullDuplexCall(pipe)
        pipe.close()
        try:
            next(response_iterator)
            raise ValueError('expected exactly 0 responses')
        except StopIteration:
            pass


def _status_code_and_message(stub):
    details = 'test status message'
    code = 2
    status = grpc.StatusCode.UNKNOWN  # code = 2

    # Test with a UnaryCall
    request = messages_pb2.SimpleRequest(
        response_type=messages_pb2.COMPRESSABLE,
        response_size=1,
        payload=messages_pb2.Payload(body=b'\x00'),
        response_status=messages_pb2.EchoStatus(code=code, message=details))
    response_future = stub.UnaryCall.future(request)
    _validate_status_code_and_details(response_future, status, details)

    # Test with a FullDuplexCall
    with _Pipe() as pipe:
        response_iterator = stub.FullDuplexCall(pipe)
        request = messages_pb2.StreamingOutputCallRequest(
            response_type=messages_pb2.COMPRESSABLE,
            response_parameters=(messages_pb2.ResponseParameters(size=1),),
            payload=messages_pb2.Payload(body=b'\x00'),
            response_status=messages_pb2.EchoStatus(code=code, message=details))
        pipe.add(request)  # sends the initial request.
    # Dropping out of with block closes the pipe
    _validate_status_code_and_details(response_iterator, status, details)


def _unimplemented_method(test_service_stub):
    response_future = (test_service_stub.UnimplementedCall.future(
        empty_pb2.Empty()))
    _expect_status_code(response_future, grpc.StatusCode.UNIMPLEMENTED)


def _unimplemented_service(unimplemented_service_stub):
    response_future = (unimplemented_service_stub.UnimplementedCall.future(
        empty_pb2.Empty()))
    _expect_status_code(response_future, grpc.StatusCode.UNIMPLEMENTED)


def _custom_metadata(stub):
    initial_metadata_value = "test_initial_metadata_value"
    trailing_metadata_value = "\x0a\x0b\x0a\x0b\x0a\x0b"
    metadata = ((_INITIAL_METADATA_KEY, initial_metadata_value),
                (_TRAILING_METADATA_KEY, trailing_metadata_value))

    def _validate_metadata(response):
        initial_metadata = dict(response.initial_metadata())
        if initial_metadata[_INITIAL_METADATA_KEY] != initial_metadata_value:
            raise ValueError('expected initial metadata %s, got %s' %
                             (initial_metadata_value,
                              initial_metadata[_INITIAL_METADATA_KEY]))
        trailing_metadata = dict(response.trailing_metadata())
        if trailing_metadata[_TRAILING_METADATA_KEY] != trailing_metadata_value:
            raise ValueError('expected trailing metadata %s, got %s' %
                             (trailing_metadata_value,
                              initial_metadata[_TRAILING_METADATA_KEY]))

    # Testing with UnaryCall
    request = messages_pb2.SimpleRequest(
        response_type=messages_pb2.COMPRESSABLE,
        response_size=1,
        payload=messages_pb2.Payload(body=b'\x00'))
    response_future = stub.UnaryCall.future(request, metadata=metadata)
    _validate_metadata(response_future)

    # Testing with FullDuplexCall
    with _Pipe() as pipe:
        response_iterator = stub.FullDuplexCall(pipe, metadata=metadata)
        request = messages_pb2.StreamingOutputCallRequest(
            response_type=messages_pb2.COMPRESSABLE,
            response_parameters=(messages_pb2.ResponseParameters(size=1),))
        pipe.add(request)  # Sends the request
        next(response_iterator)  # Causes server to send trailing metadata
    # Dropping out of the with block closes the pipe
    _validate_metadata(response_iterator)


def _compute_engine_creds(stub, args):
    response = _large_unary_common_behavior(stub, True, True, None)
    if args.default_service_account != response.username:
        raise ValueError('expected username %s, got %s' %
                         (args.default_service_account, response.username))


def _oauth2_auth_token(stub, args):
    json_key_filename = os.environ[google_auth_environment_vars.CREDENTIALS]
    wanted_email = json.load(open(json_key_filename, 'rb'))['client_email']
    response = _large_unary_common_behavior(stub, True, True, None)
    if wanted_email != response.username:
        raise ValueError('expected username %s, got %s' % (wanted_email,
                                                           response.username))
    if args.oauth_scope.find(response.oauth_scope) == -1:
        raise ValueError(
            'expected to find oauth scope "{}" in received "{}"'.format(
                response.oauth_scope, args.oauth_scope))


def _jwt_token_creds(stub, args):
    json_key_filename = os.environ[google_auth_environment_vars.CREDENTIALS]
    wanted_email = json.load(open(json_key_filename, 'rb'))['client_email']
    response = _large_unary_common_behavior(stub, True, False, None)
    if wanted_email != response.username:
        raise ValueError('expected username %s, got %s' % (wanted_email,
                                                           response.username))


def _per_rpc_creds(stub, args):
    json_key_filename = os.environ[google_auth_environment_vars.CREDENTIALS]
    wanted_email = json.load(open(json_key_filename, 'rb'))['client_email']
    google_credentials, unused_project_id = google_auth.default(
        scopes=[args.oauth_scope])
    call_credentials = grpc.metadata_call_credentials(
        google_auth_transport_grpc.AuthMetadataPlugin(
            credentials=google_credentials,
            request=google_auth_transport_requests.Request()))
    response = _large_unary_common_behavior(stub, True, False, call_credentials)
    if wanted_email != response.username:
        raise ValueError('expected username %s, got %s' % (wanted_email,
                                                           response.username))


# After a fork with a subscribed connectivity watcher:
#   Child process does not receive updates for the parent's subscribed callback
#   Child may subscribe a new connectivity watcher to the channel
def _connectivity_watch(channel):
    parent_states = []
    def parent_callback(state):
        parent_states.append(state)
    channel.subscribe(parent_callback)
    stub = test_pb2_grpc.TestServiceStub(channel)
    def child_process(child_error_queue):
        try:
            child_states = []
            def child_callback(state):
                    child_states.append(state)
            channel.subscribe(child_callback)
            _large_unary_common_behavior(stub, False, False, None)
            if len(child_states) < 2 or child_states[-1] != grpc.ChannelConnectivity.READY:
                raise ValueError('Channel did not move to READY')
            if len(parent_states) > 1:
                raise ValueError('Received connectivity updates on parent callback')
        except Exception as e:
            child_error_queue.put(str(e))
    child_error_queue = multiprocessing.Queue()
    process = multiprocessing.Process(target=child_process, args=(child_error_queue,))
    process.start()
    _large_unary_common_behavior(stub, False, False, None)
    if len(parent_states) < 2 or parent_states[-1] != grpc.ChannelConnectivity.READY:
        raise ValueError('Channel did not move to READY')
    process.join()
    try:
        child_error = child_error_queue.get(block=False)
        raise ValueError('Child process failed: %s' % child_error)
    except queue.Empty:
        pass


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

    with _Pipe() as pipe:
        stub = test_pb2_grpc.TestServiceStub(channel)
        parent_bidi_call = stub.FullDuplexCall(pipe)
        child_error_queue = multiprocessing.Queue()
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
                def child_process(child_error_queue):
                    try:
                        try:
                            parent_bidi_call.result(1)
                            raise ValueError('Received result on inherited call')
                        except grpc.FutureTimeoutError:
                            pass
                        _large_unary_common_behavior(stub, False, False, None)
                        inherited_code = parent_bidi_call.code()
                        inherited_details = parent_bidi_call.details()
                        if inherited_code != grpc.StatusCode.UNKNOWN:
                            raise ValueError('expected inherited code UNKNOWN, got %s' % inherited_code)
                        if inherited_details != 'Stream removed':
                            raise ValueError('expected inherited details Stream removed, got %s' % inherited_details)
                    except Exception as e:
                        child_error_queue.put(str(e))
                process = multiprocessing.Process(target=child_process, args=(child_error_queue,))
                process.start()
            response = next(parent_bidi_call)
            _validate_payload_type_and_length(
                response, messages_pb2.COMPRESSABLE, response_size)
            i += 1

        pipe.close()
        process.join()
        try:
            child_error = child_error_queue.get(block=False)
            raise ValueError('Child process failed: %s' % child_error)
        except queue.Empty:
            pass


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

@enum.unique
class TestCase(enum.Enum):    
    # TODO: get errors from child callbacks without this
    import logging
    import sys
    logging.getLogger('grpc').addHandler(logging.StreamHandler(sys.stdout))

    CONNECTIVITY_WATCH = 'connectivity_watch'
    IN_PROGRESS_BIDI_CONTINUE_CALL = 'in_progress_bidi_continue_call'

    def run_test(self, channel, args):
        if self is TestCase.CONNECTIVITY_WATCH:
            _connectivity_watch(channel)
        elif self is TestCase.IN_PROGRESS_BIDI_CONTINUE_CALL:
            _in_progress_bidi_continue_call(channel)
        else:
            raise NotImplementedError(
                'Test case "%s" not implemented!' % self.name)

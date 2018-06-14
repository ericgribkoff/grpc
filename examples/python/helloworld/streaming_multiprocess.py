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
"""The Python implementation of the GRPC helloworld.Greeter client."""

from __future__ import print_function

import grpc
import multiprocessing
import threading
import time

import helloworld_pb2
import helloworld_pb2_grpc


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

def doRpc():
    channel = grpc.insecure_channel('localhost:50051')
    stub = helloworld_pb2_grpc.GreeterStub(channel)
    response = stub.SayHello(helloworld_pb2.HelloRequest(name='you child'))
    print("Greeter client received: " + response.message)

def generateMessages():
    messages = [
        helloworld_pb2.HelloRequest(name='streaming request 1'),
        helloworld_pb2.HelloRequest(name='streaming request 2'),
    ]
    for msg in messages:
        yield msg

def doStreamingRpc():
    channel = grpc.insecure_channel('localhost:50051')
    stub = helloworld_pb2_grpc.GreeterStub(channel)
    #responses = stub.SayHelloStreaming(generateMessages())
    #for response in responses:
    with _Pipe() as pipe:
        response_iterator = stub.SayHelloStreaming(pipe)
        for i in range(3):
            request = helloworld_pb2.HelloRequest(name='streaming request %d' % i)
            pipe.add(request)
            response = next(response_iterator)
            print("SayHelloStreaming received: " + response.message)
            time.sleep(1)


def run():
#    channel = grpc.insecure_channel('localhost:50051')
#    stub = helloworld_pb2_grpc.GreeterStub(channel)
#    response = stub.SayHello(helloworld_pb2.HelloRequest(name='you'))
#    print("Greeter client received: " + response.message)
    #processStream = multiprocessing.Process(target=doStreamingRpc)
    #processStream.start()
    t = threading.Thread(target=doStreamingRpc)
    t.start()
    process = multiprocessing.Process(target=doRpc)
    process.start()
    process.join()

if __name__ == '__main__':
    run()

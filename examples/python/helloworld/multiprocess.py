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

def doRpc():
    channel = grpc.insecure_channel('localhost:50051')
    stub = helloworld_pb2_grpc.GreeterStub(channel)
    response = stub.SayHello(helloworld_pb2.HelloRequest(name='you child'))
    print("Greeter client received: " + response.message)

def run():
    channel = grpc.insecure_channel('localhost:50051')
    def cb(some_arg):
      print('invoked with ', some_arg)
    channel.subscribe(cb)
#    channel.unsubscribe(cb)
    def unsub():
      time.sleep(3)
      channel.unsubscribe(cb)
      print('unsubbed!')
    t = threading.Thread(target=unsub)
    t.start()
    stub = helloworld_pb2_grpc.GreeterStub(channel)
    response = stub.SayHello(helloworld_pb2.HelloRequest(name='you'))
    print("Greeter client received: " + response.message)
    process = multiprocessing.Process(target=doRpc)
    process.start()

if __name__ == '__main__':
    run()

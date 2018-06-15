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

#import grpc
import multiprocessing

#import helloworld_pb2
#import helloworld_pb2_grpc


from google.cloud import datastore


#def doRpc():
#    channel = grpc.insecure_channel('localhost:50051')
#    stub = helloworld_pb2_grpc.GreeterStub(channel)
#    response = stub.SayHello(helloworld_pb2.HelloRequest(name='you child'))
#    print("Greeter client received: " + response.message)
#
#def run():
#    channel = grpc.insecure_channel('localhost:50051')
#    stub = helloworld_pb2_grpc.GreeterStub(channel)
#    response = stub.SayHello(helloworld_pb2.HelloRequest(name='you'))
#    print("Greeter client received: " + response.message)
#    process = multiprocessing.Process(target=doRpc)
#    process.start()


def causeTrouble(where):
    client = datastore.Client(project='some-project', namespace='kind')
    print(client.get(client.key('kind', 'name')))
    # The call to get() hangs forever; this line is never reached.
    print('OK')


if __name__ == '__main__':
    # Create a datastore client and do an RPC on it.
    client = datastore.Client(project='some-project')
    print(client.get(client.key('kind', 'name')))

    # Kick off a child process while the first client is still in scope.
    process = multiprocessing.Process(target=causeTrouble,
                                      args=['child process'])
    process.start()
#    run()

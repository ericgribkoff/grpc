"""The Python implementation of the GRPC helloworld.Greeter client."""

from __future__ import print_function

import grpc
import multiprocessing
import sys
import time

import helloworld_pb2
import helloworld_pb2_grpc

def do_multiprocess():
  make_channel_with_message()
  time.sleep(1)

def make_channel_with_message():
  print("Trying to make child channel")
  channel = grpc.insecure_channel('localhost:50053')
  stub = helloworld_pb2_grpc.GreeterStub(channel)
  response = stub.SayHello(helloworld_pb2.HelloRequest(name='you child'))
  print("Greeter client received: " + response.message)

if __name__ == '__main__':
  channel = grpc.insecure_channel('localhost:50051')
  stub = helloworld_pb2_grpc.GreeterStub(channel)
  response = stub.SayHello(helloworld_pb2.HelloRequest(name='OG'))
  print("Greeter client received: " + response.message)
  for i in range(int(sys.argv[1])):
#    make_channel_with_message()
    process = multiprocessing.Process(target=do_multiprocess)
    process.start()
    #process.join()
    if i % 100 == 0:
      response = stub.SayHello(helloworld_pb2.HelloRequest(name=str(i)))
      print("Greeter client received: " + response.message)
      #print(time.time())

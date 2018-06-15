"""The Python implementation of the GRPC helloworld.Greeter client."""

from __future__ import print_function

import grpc
import subprocess32
import time


def call_subproc():
  o = subprocess32.check_output(["echo", "1"])

def make_channel_with_message():
  channel = grpc.insecure_channel('localhost:50051')

if __name__ == '__main__':
  subprocess32.check_output(["echo", "Hello World!"])
  for i in range(1000000):
    call_subproc()
    make_channel_with_message()
    if i % 100 == 0:
      print(time.time())

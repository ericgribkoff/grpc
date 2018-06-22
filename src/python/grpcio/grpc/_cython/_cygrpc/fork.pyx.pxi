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

#
import threading
import time #TODO remove
import traceback

cdef void __prefork() nogil:
  with gil:
    with thread_count.forking_lock:
      thread_count.forking = True
    traceback.print_stack()
    print('awaiting tb.num_threads=0')
    print('tb.num_threads=0? ', thread_count.await_zero_threads(5))

cdef void __postfork() nogil:
  with gil:
    with thread_count.forking_lock:
      thread_count.forking = False

fork_handler_lock = threading.Lock()
fork_handlers_registered = False

def fork_handlers_and_grpc_init():
  global fork_handler_lock
  global fork_handlers_registered
  grpc_init()
  with fork_handler_lock:
    if not fork_handlers_registered:
      print('Installing fork handler after grpc_init',
        pthread_atfork(&__prefork,
        &__postfork, &__postfork))
      fork_handlers_registered = True

class ThreadCount(object):
  def __init__(self):
    self.num_threads = 0
    self.lock = threading.Condition()
    self.forking = False
    self.forking_lock = threading.RLock()

  def increment(self):
    print('incrementing thread count')
    traceback.print_stack()
    self.lock.acquire()
    self.num_threads += 1
    self.lock.release()

  def decrement(self):
    print('decrementing thread count')
    traceback.print_stack()
    self.lock.acquire()
    if self.num_threads > 0:
      self.num_threads -= 1
    # TODO else error
    if self.num_threads == 0:
      self.lock.notify_all()
    self.lock.release()

  def await_zero_threads(self, timeout_secs):
    with self.lock:
      if self.num_threads > 0:
        self.lock.wait(timeout_secs)
      return self.num_threads == 0

thread_count = ThreadCount()

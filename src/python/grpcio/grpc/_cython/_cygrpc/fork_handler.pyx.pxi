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

import threading
import time #TODO remove
import traceback

cdef void __prefork() nogil:
  with gil:
#    def waitAndUnlock():
#      time.sleep(3)
#      print('waking up and finishing!')
#      thread_barrier.decrement_threads()
#    t = threading.Thread(target=waitAndUnlock)
#    thread_barrier.increment_threads()
#    t.start()
#    print('num_threads went to 0')
    traceback.print_exc()
    print('invoked?')
    print('awaiting tb.num_threads=0')
    print(thread_barrier.await_zero_threads(5))
    print('tb.num_threads=0')

class ThreadBarrier(object):
  def __init__(self):
    self.num_threads = 0
    self.lock = threading.Condition()

  def increment_threads(self):
    self.lock.acquire()
    self.num_threads += 1
    self.lock.release()

  def decrement_threads(self):
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

thread_barrier = ThreadBarrier()

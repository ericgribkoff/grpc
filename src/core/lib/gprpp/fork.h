/*
 *
 * Copyright 2017 gRPC authors.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 */

#ifndef GRPC_CORE_LIB_GPRPP_FORK_H
#define GRPC_CORE_LIB_GPRPP_FORK_H

/*
 * NOTE: FORKING IS NOT GENERALLY SUPPORTED, THIS IS ONLY INTENDED TO WORK
 *       AROUND VERY SPECIFIC USE CASES.
 */

namespace grpc_core {

namespace internal {
class ExecCtxState;
class ThreadState;
}  // namespace internal

class Fork {
 public:
  static void GlobalInit();
  static void GlobalShutdown();

  // Returns true if fork suppport is enabled, false otherwise
  static bool Enabled();

  // Increment the count of active ExecCtxs.
  // Will block until a pending fork is complete if one is in progress.
  static void IncExecCtxCount();

  // Decrement the count of active ExecCtxs
  static void DecExecCtxCount();

  static void IncrementForkEpoch();

  static int GetForkEpoch();

  // Check if there is a single active ExecCtx
  // (the one used to invoke this function).  If there are more,
  // return false.  Otherwise, return true and block creation of
  // more ExecCtx s until AlloWExecCtx() is called
  //
  static bool BlockExecCtx();
  static void AllowExecCtx();

  // Increment the count of active threads.
  static void IncThreadCount();

  // Decrement the count of active threads.
  static void DecThreadCount();

  // Await all core threads to be joined.
  static void AwaitThreads();

  // Test only: overrides environment variables/compile flags
  // Must be called before grpc_init()
  static void Enable(bool enable);

 private:
  static internal::ExecCtxState* execCtxState_;
  static internal::ThreadState* threadState_;
  static bool supportEnabled_;
  static bool overrideEnabled_;
  static int forkEpoch_;
};

}  // namespace grpc_core

#endif /* GRPC_CORE_LIB_GPRPP_FORK_H */

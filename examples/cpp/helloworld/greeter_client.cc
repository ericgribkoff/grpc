/*
 *
 * Copyright 2015 gRPC authors.
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

#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#include <chrono>
#include <iostream>
#include <memory>
#include <string>
#include <thread>

#include <grpcpp/grpcpp.h>
#include "../../../src/core/lib/gpr/env.h"

#ifdef BAZEL_BUILD
#include "examples/protos/helloworld.grpc.pb.h"
#else
#include "helloworld.grpc.pb.h"
#endif

using grpc::Channel;
using grpc::ClientContext;
using grpc::Status;
using helloworld::Greeter;
using helloworld::HelloReply;
using helloworld::HelloRequest;

class GreeterClient {
 public:
  GreeterClient(std::shared_ptr<Channel> channel)
      : stub_(Greeter::NewStub(channel)) {}

  // Assembles the client's payload, sends it and presents the response back
  // from the server.
  std::string SayHello(const std::string& user) {
    // Data we are sending to the server.
    HelloRequest request;
    request.set_name(user);

    // Container for the data we expect from the server.
    HelloReply reply;

    // Context for the client. It could be used to convey extra information to
    // the server and/or tweak certain RPC behaviors.
    ClientContext context;

    // The actual RPC.
    Status status = stub_->SayHello(&context, request, &reply);

    // Act upon its status.
    if (status.ok()) {
      return reply.message();
    } else {
      std::cout << status.error_code() << ": " << status.error_message()
                << std::endl;
      return "RPC failed";
    }
  }

  void StreamingHello(int interations, int interval) {
    ClientContext context;

    std::shared_ptr<grpc::ClientReaderWriter<HelloRequest, HelloReply> > stream(
        stub_->SayHelloStreaming(&context));

    for (int i = 0; i < interations; i++) {
      HelloRequest request;
      request.set_name(std::to_string(i));
      if (!stream->Write(request)) {
        break;
      }
      std::cout << "Sleeping for " << interval << " seconds" << std::endl;
      std::this_thread::sleep_for(std::chrono::seconds(interval));
      HelloReply reply;
      if (!stream->Read(&reply)) {
        break;
      }
      std::cout << "Got message " << reply.message() << std::endl;
    }
    stream->WritesDone();
    std::cout << "StreamingHello done" << std::endl;

    Status status = stream->Finish();
    std::cout << "Status received" << std::endl;
    if (!status.ok()) {
      std::cout << "Streaming rpc failed." << std::endl;
      std::cout << status.error_code() << std::endl;
      std::cout << status.error_message() << std::endl;
      std::cout << status.error_details() << std::endl;
    }
  }

 private:
  std::unique_ptr<Greeter::Stub> stub_;
};

void keepAlive() {
  grpc::ChannelArguments chan_args;
  chan_args.SetInt(GRPC_ARG_KEEPALIVE_TIME_MS, 10000);
  chan_args.SetInt(GRPC_ARG_KEEPALIVE_TIMEOUT_MS, 10000);
  chan_args.SetInt(GRPC_ARG_HTTP2_MIN_SENT_PING_INTERVAL_WITHOUT_DATA_MS,
                   10000);
  chan_args.SetInt(GRPC_ARG_KEEPALIVE_PERMIT_WITHOUT_CALLS, 1);
  std::shared_ptr<Channel> channel = grpc::CreateCustomChannel(
      "localhost:50051", grpc::InsecureChannelCredentials(), chan_args);

  GreeterClient* greeter = new GreeterClient(channel);
  std::string user("world2");
  std::string replyStr = greeter->SayHello(user);
  std::cout << "Greeter received: " << replyStr << std::endl;

  std::thread streamer([greeter]() { greeter->StreamingHello(5, 3); });

  std::cout << "Original process ID: " << ::getpid() << std::endl;
  if (fork() != 0) {
    std::cout << "Parent process ID: " << ::getpid() << std::endl;
    std::this_thread::sleep_for(std::chrono::seconds(2));
    std::string parent_reply = greeter->SayHello("parent");
    std::cout << "Parent received: " << parent_reply << std::endl;

    int status;
    pid_t pid = wait(&status);
    std::cout << "(" << ::getpid() << ") Child process is done" << std::endl;

    streamer.join();
    std::cout << "(" << ::getpid() << ") Streamer thread is done" << std::endl;
  } else {
    std::cout << "Child process ID: " << ::getpid() << std::endl;
    std::this_thread::sleep_for(std::chrono::seconds(20));
    streamer.detach();
  }
}

void newChannelSameArgs() {
  std::shared_ptr<Channel> channel = grpc::CreateChannel(
      "localhost:50051", grpc::InsecureChannelCredentials());
  GreeterClient* greeter = new GreeterClient(channel);
  std::string user("world2");
  std::string replyStr = greeter->SayHello(user);
  std::cout << "Greeter received: " << replyStr << std::endl;

  std::thread streamer([greeter]() { greeter->StreamingHello(3, 1); });

  std::cout << "Original process ID: " << ::getpid() << std::endl;
  if (fork() != 0) {
    std::cout << "Parent process ID: " << ::getpid() << std::endl;
    std::this_thread::sleep_for(std::chrono::seconds(2));
    std::string parent_reply = greeter->SayHello("parent");
    std::cout << "Parent received: " << parent_reply << std::endl;

    int status;
    pid_t pid = wait(&status);
    std::cout << "(" << ::getpid() << ") Child process is done" << std::endl;

    streamer.join();
    std::cout << "(" << ::getpid() << ") Streamer thread is done" << std::endl;
  } else {
    std::cout << "Child process ID: " << ::getpid() << std::endl;
    std::shared_ptr<Channel> child_channel = grpc::CreateChannel(
        "localhost:50051", grpc::InsecureChannelCredentials());
    GreeterClient* child_greeter = new GreeterClient(child_channel);
    std::string child_user("child");
    std::cout << "Child received: " << child_greeter->SayHello(child_user)
              << std::endl;
    streamer.detach();
  }
}

void continueCallInChild() {
  std::shared_ptr<Channel> channel = grpc::CreateChannel(
      "localhost:50051", grpc::InsecureChannelCredentials());
  GreeterClient* greeter = new GreeterClient(channel);

  ClientContext context;
  std::shared_ptr<grpc::ClientReaderWriter<HelloRequest, HelloReply> > stream(
      Greeter::NewStub(channel)->SayHelloStreaming(&context));

  HelloRequest request;
  request.set_name("before fork stream");
  if (!stream->Write(request)) {
    std::cout << "Error writing" << std::endl;
  }
  HelloReply reply;
  if (stream->Read(&reply)) {
    std::cout << "Got message " << reply.message() << std::endl;
  }
  std::cout << "Original process ID: " << ::getpid() << std::endl;
  if (fork() != 0) {
    std::cout << "Parent process ID: " << ::getpid() << std::endl;
    std::this_thread::sleep_for(std::chrono::seconds(2));

    HelloRequest request2;
    request2.set_name("after fork stream");
    if (!stream->Write(request2)) {
      std::cout << "Error writing" << std::endl;
    }
    HelloReply reply2;
    if (stream->Read(&reply2)) {
      std::cout << "Got message " << reply2.message() << std::endl;
    }

    stream->WritesDone();
    std::cout << "(parent) StreamingHello done" << std::endl;

    Status grpcStatus = stream->Finish();
    std::cout << "(parent) Status received" << std::endl;
    if (!grpcStatus.ok()) {
      std::cout << "(parent) Streaming rpc failed." << std::endl;
      std::cout << grpcStatus.error_code() << std::endl;
      std::cout << grpcStatus.error_message() << std::endl;
      std::cout << grpcStatus.error_details() << std::endl;
    }

    int status;
    pid_t pid = wait(&status);
    std::cout << "(" << ::getpid() << ") Child process is done" << std::endl;
  } else {
    std::cout << "Child process ID: " << ::getpid() << std::endl;
    stream->WritesDone();
    std::cout << "(child) StreamingHello done" << std::endl;

    Status grpcStatus = stream->Finish();
    std::cout << "(child) Status received" << std::endl;
    if (!grpcStatus.ok()) {
      std::cout << "(child) Streaming rpc failed." << std::endl;
      std::cout << grpcStatus.error_code() << std::endl;
      std::cout << grpcStatus.error_message() << std::endl;
      std::cout << grpcStatus.error_details() << std::endl;
    }
  }
}

void reuseChannelInChild() {
  std::shared_ptr<Channel> channel = grpc::CreateChannel(
      "localhost:50051", grpc::InsecureChannelCredentials());
    // std::this_thread::sleep_for(std::chrono::seconds(10));

  GreeterClient* greeter = new GreeterClient(channel);
  std::string user("world2");
  std::string replyStr = greeter->SayHello(user);
  std::cout << "Greeter received: " << replyStr << std::endl;

  std::thread streamer([greeter]() { greeter->StreamingHello(3, 3); });

  std::cout << "Original process ID: " << ::getpid() << std::endl;
  if (fork() != 0) {
    std::cout << "Parent process ID: " << ::getpid() << std::endl;
    std::this_thread::sleep_for(std::chrono::seconds(1));
    std::string parent_reply = greeter->SayHello("parent");
    std::cout << "Parent received: " << parent_reply << std::endl;

    int status;
    pid_t pid = wait(&status);
    std::cout << "(" << ::getpid() << ") Child process is done" << std::endl;

    streamer.join();
    std::cout << "(" << ::getpid() << ") Streamer thread is done" << std::endl;
  } else {
    std::cout << "Child process ID: " << ::getpid() << std::endl;
    std::string child_same_channel_reply =
        greeter->SayHello("child same channel");
    std::cout << "Child received: " << child_same_channel_reply << std::endl;
    std::this_thread::sleep_for(std::chrono::seconds(2));
    child_same_channel_reply =
        greeter->SayHello("child same channel attempt 2");
    std::cout << "Child received: " << child_same_channel_reply << std::endl;
    streamer.detach();
  }
}

void cleanShutdown() {
  std::shared_ptr<Channel> channel = grpc::CreateChannel(
      "localhost:50051", grpc::InsecureChannelCredentials());
  GreeterClient* greeter = new GreeterClient(channel);
  std::string user("world2");
  std::string replyStr = greeter->SayHello(user);
  std::cout << "Greeter received: " << replyStr << std::endl;

  std::cout << "Original process ID: " << ::getpid() << std::endl;
  if (fork() != 0) {
    std::cout << "Parent process ID: " << ::getpid() << std::endl;
    std::this_thread::sleep_for(std::chrono::seconds(3));
    std::string parent_reply = greeter->SayHello("parent");
    std::cout << "Parent received: " << parent_reply << std::endl;

    int status;
    pid_t pid = wait(&status);
    std::cout << "(" << ::getpid() << ") Child process is done" << std::endl;

    std::cout << "(" << ::getpid() << ") Streamer thread is done" << std::endl;
  } else {
    std::cout << "Child process ID: " << ::getpid() << std::endl;
    delete greeter;
    channel.reset();
    std::this_thread::sleep_for(std::chrono::seconds(5));
  }
}

int main(int argc, char** argv) {
  // Test cases:
  //   keep_alive - pings shouldn't continue in child
  //   new_channel_same_args - child should be able to create new channel
  //     without using the same subchannel from parent
  //   continue_call_in_child - should return error in child, succeed in parent
  //   reuse_channel_in_child - should reconnect and succeed in child
  //   clean_shutdown - fork should not prevent clean gRPC shutdown in child

  std::string test_case;
  if (argc > 0) {
    test_case = argv[1];
  }

  if (test_case == "keep_alive") {
    std::cout << "Running test case keep_alive" << std::endl;
    keepAlive();
  } else if (test_case == "new_channel_same_args") {
    std::cout << "Running test case new_channel_same_args" << std::endl;
    newChannelSameArgs();
  } else if (test_case == "continue_call_in_child") {
    std::cout << "Running test case continue_call_in_child" << std::endl;
    continueCallInChild();
  } else if (test_case == "reuse_channel_in_child") {
    std::cout << "Running test case reuse_channel_in_child" << std::endl;
    reuseChannelInChild();
  } else if (test_case == "clean_shutdown") {
    std::cout << "Running test case clean_shutdown" << std::endl;
    cleanShutdown();
  } else {
    std::cout << "Unknown test case" << std::endl;
  }
}

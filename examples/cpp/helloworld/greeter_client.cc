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

#include <iostream>
#include <memory>
#include <string>
#include <chrono>
#include <thread>
#include <unistd.h>
#include <sys/types.h>
#include <sys/wait.h>


#include "../../../src/core/lib/gpr/env.h"
#include <grpcpp/grpcpp.h>

#ifdef BAZEL_BUILD
#include "examples/protos/helloworld.grpc.pb.h"
#else
#include "helloworld.grpc.pb.h"
#endif

using grpc::Channel;
using grpc::ClientContext;
using grpc::Status;
using helloworld::HelloRequest;
using helloworld::HelloReply;
using helloworld::Greeter;

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

  void StreamingHello() {
    ClientContext context;

    std::shared_ptr<grpc::ClientReaderWriter<HelloRequest, HelloReply> > stream(
        stub_->SayHelloStreaming(&context));

    for (int i = 0; i < 3; i++) {
      HelloRequest request;
      request.set_name(std::to_string(i));
      if (!stream->Write(request)) {
        break;
      }
      HelloReply reply;
      if (!stream->Read(&reply)) {
        break;
      }
      std::cout << "Got message " << reply.message() << std::endl;
      //context.TryCancel();
      std::this_thread::sleep_for(std::chrono::seconds(3));
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
//    std::cout << "Returning without calling Finish()" << std::endl;
  }

 private:
  std::unique_ptr<Greeter::Stub> stub_;
};

void doRpc(std::string str) {
  GreeterClient greeter(grpc::CreateChannel(
      "localhost:50051", grpc::InsecureChannelCredentials()));
  std::cout << "doRpc: Channel created" << std::endl;
  std::string user(str);
  std::string reply = greeter.SayHello(user);
  std::cout << "doRpc: Greeter received: " << reply << std::endl;
}

void doRpcAndWait(std::string str) {
  GreeterClient greeter(grpc::CreateChannel(
      "localhost:50051", grpc::InsecureChannelCredentials()));
  std::cout << "Channel created" << std::endl;
  std::string user(str);
  std::string reply = greeter.SayHello(user);
  std::cout << "Greeter received: " << reply << std::endl;
  std::this_thread::sleep_for(std::chrono::seconds(10));
}

int main(int argc, char** argv) {
  // Instantiate the client. It requires a channel, out of which the actual RPCs
  // are created. This channel models a connection to an endpoint (in this case,
  // localhost at port 50051). We indicate that the channel isn't authenticated
  // (use of InsecureChannelCredentials()).
  //grpc_init();
  std::shared_ptr<Channel> channel = grpc::CreateChannel(
      "localhost:50051", grpc::InsecureChannelCredentials());
  GreeterClient *greeter = new GreeterClient(channel);
  std::string user("world2");
  std::string reply = greeter->SayHello(user);
  std::cout << "Greeter received: " << reply << std::endl;
  //exit(0);
  //channel->EnterLame();
  //std::string reply2 = greeter->SayHello("second time");
  //std::cout << "Greeter received: " << reply2 << std::endl;
//  std::thread streamer([greeter]() {
//    greeter->StreamingHello();
//  });
//  std::this_thread::sleep_for(std::chrono::seconds(1));
//  channel->EnterLame();
//  streamer.join();
//  exit(1);
////  std::cout << "Done sleeping" << std::endl;
////  std::string reply2 = greeter->SayHello("parent");
////  std::cout << "Received: " << reply2 << std::endl;
//  streamer.join();
////  delete greeter;
//
//  std::cout << "Sleeping for 1 more second after join" << std::endl;
//  std::this_thread::sleep_for(std::chrono::seconds(1));
//  exit(0);
  //grpc_shutdown();
  //grpc_init();
  //grpc_shutdown();
  std::cout << "Original process ID: " << ::getpid() << std::endl;
  if (fork() != 0) {
    std::cout << "Parent process ID: " << ::getpid() << std::endl;
    greeter->SayHello("parent");
    //doRpc("parent");
//    streamer.join();
//    std::cout << "(" << ::getpid() << ") Streamer thread is done" << std::endl;

    int status;
    pid_t pid = wait(&status);
    std::cout << "(" << ::getpid() << ") Child process is done" << std::endl;
  } else {
    std::cout << "Child process ID: " << ::getpid() << std::endl;
//    channel->EnterLame();
    //std::cout << "Forked: " << greeter->SayHello("blah") << std::endl;
    //gpr_setenv("GRPC_VERBOSITY", "DEBUG");
    //gpr_setenv("GRPC_TRACE", "all");
    //doRpcAndWait("child");
    doRpc("child");
//    streamer.detach();
  }

//  std::cout << "Forked: " << greeter.SayHello("blah") << std::endl;


  return 0;
}

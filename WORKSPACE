bind(
    name = "nanopb",
    actual = "//third_party/nanopb",
)

bind(
    name = "libssl",
    actual = "@submodule_boringssl//:ssl",
)

bind(
    name = "zlib",
    actual = "@submodule_zlib//:z",
)

bind(
    name = "protobuf",
    actual = "@com_google_protobuf//:protobuf",
)

bind(
    name = "protobuf_clib",
    actual = "@com_google_protobuf//:protoc_lib",
)

bind(
    name = "protocol_compiler",
    actual = "@com_google_protobuf//:protoc",
)

bind(
    name = "gtest",
    actual = "@submodule_gtest//:gtest",
)

bind(
    name = "gflags",
    actual = "@com_github_gflags_gflags//:gflags",
)

new_local_repository(
    name = "submodule_boringssl",
    build_file = "third_party/boringssl-with-bazel/BUILD",
    path = "third_party/boringssl-with-bazel",
)

new_local_repository(
    name = "submodule_zlib",
    build_file = "third_party/zlib.BUILD",
    path = "third_party/zlib",
)

new_local_repository(
    name = "com_google_protobuf",
    build_file = "third_party/protobuf/BUILD",
    path = "third_party/protobuf",
)

new_local_repository(
    name = "submodule_gtest",
    build_file = "third_party/gtest.BUILD",
    path = "third_party/googletest",
)

local_repository(
    name = "com_github_gflags_gflags",
    path = "third_party/gflags",
)

# used for tools/grpcz/grpcz_client
git_repository(
    name = "mongoose_repo",
    commit = "4120a97945b41195a6223a600dae8e3b19bed19e",
    remote = "https://github.com/makdharma/mongoose.git",
)

git_repository(
    name = "grpc_proto_repo",
    commit = "3fbebb2d7be7b062586f3eedf4d61ec6bf39c48b",
    remote = "https://github.com/ericgribkoff/grpc-proto.git",
)

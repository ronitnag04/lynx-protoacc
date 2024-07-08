#include <iostream>
#include <fstream>
#include <string>
#include <cstdio>
#include <cinttypes>
#include <chrono>

#include "primitives.pb.h"

#ifdef __riscv
#include "accellib.h"
#endif

using namespace std;


int main() {
    GOOGLE_PROTOBUF_VERIFY_VERSION;

    std::cout << "s1\n" << std::flush;

#ifdef __riscv
    string hostplat = "riscv";
#else
    string hostplat = "x86";
#endif

    std::cout << "s2\n" << std::flush;

#define NUMTESTVALS 1
    string testvals[NUMTESTVALS] = {  "hello" };

    std::cout << "s3\n" << std::flush;

    google::protobuf::Arena arena;

    primitivetests::Paccser_bytes_repeatedMessage* fillmessage = google::protobuf::Arena::CreateMessage<primitivetests::Paccser_bytes_repeatedMessage>(&arena);

    fillmessage->add_paccbytes_repeated_0(testvals[0]);
    fillmessage->add_paccbytes_repeated_0(testvals[0]);
    fillmessage->add_paccbytes_repeated_0(testvals[0]);

    std::cout << "Ptrs:" << std::endl;
    std::cout << "  obj: "                                               << fillmessage << std::endl;

#ifdef __riscv
    asm volatile ("addi x0, x1, 0");
#endif
    //std::cout << "  obj._impl_: "                                        << &fillmessage->_impl_ << std::endl;
    //std::cout << "  obj._impl_.paccbytes_repeated: "                     << &fillmessage->_impl_.paccbytes_repeated_0_ << std::endl;
    //std::cout << "  obj._impl_.paccbytes_repeated.tagged_rep_or_elem_: " << &fillmessage->_impl_.paccbytes_repeated_0_.tagged_rep_or_elem_ << std::endl;
    //std::cout << "  obj._impl_.paccbytes_repeated.current_size_:  "      << &fillmessage->_impl_.paccbytes_repeated_0_.current_size_ << std::endl;
    //std::cout << "  obj._impl_.paccbytes_repeated.capacity_proxy_:  "    << &fillmessage->_impl_.paccbytes_repeated_0_.capacity_proxy_ << std::endl;
    //std::cout << "  obj._impl_.paccbytes_repeated.arena_:  "             << &fillmessage->_impl_.paccbytes_repeated_0_.arena_ << std::endl;

    google::protobuf::ShutdownProtobufLibrary();
    return 0;
}

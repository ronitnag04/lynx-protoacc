#!/usr/bin/env python3

class PrimitiveInfo:
    def __init__(self, testname, primname, cpptype, testvals, is_repeated=False, nestedtype=None):
        self.testname = testname
        self.primname = primname
        self.cpptype = cpptype
        self.testvals = testvals
        self.is_repeated = is_repeated
        self.nestedtype = nestedtype
        if self.nestedtype is not None and self.is_repeated:
            exit(1)

    def get_test_name(self):
        return self.testname

    def generate_testvals_initialized(self):
        numtests = max(len(self.testvals), 1)
        numtestsnonmin = len(self.testvals)
        comma_separated_testvals = ", ".join(self.testvals)

        msg = """#define NUMTESTVALS {NUMTESTS}
#define NUMTESTVALS_NON_MIN {NUMTESTS_NON_MIN}
{CPPTYPE} testvals[NUMTESTVALS] = {{  {INPUTVALS} }};
""".format(NUMTESTS=numtests, NUMTESTS_NON_MIN=numtestsnonmin, CPPTYPE=self.cpptype, INPUTVALS=comma_separated_testvals)
        return msg

    def get_fieldname_single_ident(self):
        if not self.is_repeated:
            return self.primname
        return self.primname + "_repeated"

    def get_nested_fieldname_single_ident(self):
        return self.nestedtype

    def get_fieldtype_spaces(self):
        if not self.is_repeated:
            return "optional " + self.primname
        return "repeated " + self.primname

    def get_message_name(self):
        return """Pacc{TESTNAME}Message""".format(TESTNAME=self.get_test_name())

    def get_allocator_line(self):
        msg = """primitivetests::{MESSAGENAME}* fillmessage = google::protobuf::Arena::CreateMessage<primitivetests::{MESSAGENAME}>(&arena);""".format(FIELDTYPE=self.get_fieldname_single_ident(), MESSAGENAME=self.get_message_name())
        msg2 = ""
        if self.nestedtype is not None:
            msg2 = """\n            primitivetests::Pacc{FIELDTYPE}Message* nested_message = google::protobuf::Arena::CreateMessage<primitivetests::Pacc{FIELDTYPE}Message>(&arena);""".format(FIELDTYPE=self.get_nested_fieldname_single_ident(), MESSAGENAME=self.get_message_name())
        return msg + msg2

    def get_setter_lines(self):
        numsets = len(self.testvals)
        retmsg = """"""
        for s in range(numsets):
            if self.is_repeated:
                retmsg += "            " + self.get_setter_line(0, s) + "\n"
            else:
                retmsg += "            " + self.get_setter_line(s, s) + "\n"
        return retmsg

    def get_setter_line(self, fieldindex, testidx):
        if self.is_repeated:
            msg = """fillmessage->add_pacc{FIELDTYPE}_{FIELDINDEX}(testvals[{TESTINDEX}]);""".format(FIELDTYPE=self.get_fieldname_single_ident(), FIELDINDEX=fieldindex, TESTINDEX=testidx)
        elif self.nestedtype is not None:
            msg = """nested_message->set_pacc{NESTEDFIELDTYPE}_{FIELDINDEX}(testvals[{FIELDINDEX}]);
            fillmessage->set_allocated_pacc{FIELDTYPE}_{FIELDINDEX}(nested_message);
""".format(NESTEDFIELDTYPE=self.get_nested_fieldname_single_ident(),
                       FIELDTYPE=self.get_fieldname_single_ident().lower(),
           FIELDINDEX=fieldindex)

        else:
            msg = """fillmessage->set_pacc{FIELDTYPE}_{FIELDINDEX}(testvals[{FIELDINDEX}]);""".format(FIELDTYPE=self.get_fieldname_single_ident(), FIELDINDEX=fieldindex)

        return msg

    def get_getter(self, fieldindex, elemidx, wo_elemidx=False):
        if self.is_repeated:
            if wo_elemidx:
                msg = """pacc{FIELDTYPE}_{FIELDINDEX}""".format(FIELDTYPE=self.get_fieldname_single_ident(), FIELDINDEX=fieldindex)
            else:
                msg = """pacc{FIELDTYPE}_{FIELDINDEX}({ELEMIDX})""".format(FIELDTYPE=self.get_fieldname_single_ident(), FIELDINDEX=fieldindex, ELEMIDX=elemidx)
        elif self.nestedtype is not None:
            msg = """pacc{NESTEDFIELDTYPE}_{FIELDINDEX}().pacc{FIELDTYPE}_{FIELDINDEX}()""".format(NESTEDFIELDTYPE=self.get_fieldname_single_ident().lower(),
                                                                         FIELDTYPE=self.get_nested_fieldname_single_ident(), FIELDINDEX=fieldindex)
        else:
            msg = """pacc{FIELDTYPE}_{FIELDINDEX}()""".format(FIELDTYPE=self.get_fieldname_single_ident(), FIELDINDEX=fieldindex)

        return msg

    def get_hascheck(self, fieldindex):
        if self.is_repeated:
            msg = """|| true"""
        elif self.nestedtype is not None:
            msg = """->has_pacc{NESTEDFIELDTYPE}_{FIELDINDEX}()""".format(NESTEDFIELDTYPE=self.get_fieldname_single_ident().lower(), FIELDINDEX=fieldindex)
        else:
            msg = """->has_pacc{FIELDTYPE}_{FIELDINDEX}()""".format(FIELDTYPE=self.get_fieldname_single_ident(), FIELDINDEX=fieldindex)

        return msg

    def get_check(self, lm, rm, failmsg):
        if self.is_repeated:
            msg = """
                for (auto i = 0; i < NUMTESTVALS_NON_MIN; i++) {{
                    if ({lm}->{GETTER_NON_IDX}(i) != {rm}->{GETTER_NON_IDX}(i)) {{
                        {MSG}
                        exit(1);
                    }}
                }}
            """.format(lm=lm, rm=rm, MSG=failmsg, GETTER_NON_IDX=self.get_getter(0, 0, True))
        else:
            msg = """
                if ({lm}->{GETTER} != {rm}->{GETTER}) {{
                    {MSG}
                    exit(1);
                }}
            """.format(lm=lm, rm=rm, MSG=failmsg, GETTER=self.get_getter(0, 0))
        return msg

    def get_fencecheck(self):
        if not self.is_repeated:
            msg = """"""
            for x in range(len(self.testvals)):
                msg += """|| (parseintos[ITERS-1]->{GETTER} != fillmessage->{GETTER})  """.format(GETTER=self.get_getter(x, x))
            return msg[2:]
        else:
            msg = """"""
            for x in range(len(self.testvals)):
                msg += """|| (parseintos[ITERS-1]->pacc{FIELDTYPE}_{FIELDINDEX}_size() == 0 && (parseintos[ITERS-1]->{GETTER} != fillmessage->{GETTER})) """.format(GETTER=self.get_getter(0, x), FIELDTYPE=self.get_fieldname_single_ident(), FIELDINDEX=0)
            if len(self.testvals) == 0:
                return "0";
            else:
                return msg[2:]
        return msg

    def gen_simple_message(self):
        msg = """
message {MESSAGENAME} {{
""".format(FIELDTYPE=self.get_fieldname_single_ident(), MESSAGENAME=self.get_message_name())
        for x in range(max(len(self.testvals), 1)):
            msg += """  {PROPERFIELDTYPE} pacc{FIELDTYPE}_{FIELDINDEX} = {FIELDNO};
""".format(PROPERFIELDTYPE=self.get_fieldtype_spaces(), FIELDTYPE=self.get_fieldname_single_ident(),
           FIELDINDEX=x, FIELDNO=x+1)
        msg += """}"""
        return msg

gen_test_fields = 5

primitive_info_objs = [
    PrimitiveInfo("double", "double", "double", ["1.0" for _ in range(gen_test_fields)]),
    PrimitiveInfo("double_repeated", "double", "double", ["1.0"], is_repeated=True),
    PrimitiveInfo("PaccdoubleMessage", "PaccdoubleMessage", "double", ["1.0"], is_repeated=False, nestedtype="double"),

    PrimitiveInfo("float", "float", "float", ["1.0" for _ in range(gen_test_fields)]),
    PrimitiveInfo("float_repeated", "float", "float", ["1.0"], is_repeated=True),

    PrimitiveInfo("int32", "int32", "int32_t", ["1"]),
    PrimitiveInfo("int32_repeated", "int32", "int32_t", ["1"], is_repeated=True),

    PrimitiveInfo("int64", "int64", "int64_t", ["1"]),
    PrimitiveInfo("int64_repeated", "int64", "int64_t", ["1"], is_repeated=True),

    PrimitiveInfo("uint32", "uint32", "uint32_t", ["1"]),

    PrimitiveInfo("sint32", "sint32", "int32_t", ["1"]),
    PrimitiveInfo("sint64", "sint64", "int64_t", ["1"]),
    PrimitiveInfo("sint64_repeated", "sint64", "int64_t", ["1"], is_repeated=True),

    PrimitiveInfo("fixed32", "fixed32", "uint32_t", ["1" for _ in range(gen_test_fields)]),
    PrimitiveInfo("fixed64", "fixed64", "uint64_t", ["1" for _ in range(gen_test_fields)]),
    PrimitiveInfo("sfixed32", "sfixed32", "int32_t", ["1" for _ in range(gen_test_fields)]),
    PrimitiveInfo("sfixed64", "sfixed64", "int64_t", ["1" for _ in range(gen_test_fields)]),
    PrimitiveInfo("bool", "bool", "bool", ["true" for _ in range(gen_test_fields)]),
    PrimitiveInfo("PaccboolMessage", "PaccboolMessage", "bool", ["true"], is_repeated=False, nestedtype="bool"),

    PrimitiveInfo("bool_repeated", "bool", "bool", ["true"], is_repeated=True),

    PrimitiveInfo("string", "string", "string", ['"hello"']),
    PrimitiveInfo("string_long", "string", "string", ['"hello hello hello hello hello hello hello"']),
    PrimitiveInfo("string_15", "string", "string", ['"hello hello he"']),
    PrimitiveInfo("string_very_long", "string", "string", ['"' + ("a" * 489) + '"']),

    PrimitiveInfo("PaccstringMessage", "PaccstringMessage", "string", ['"hello"'], is_repeated=False, nestedtype="string"),

    PrimitiveInfo("bytes", "bytes", "string", ['"hello"']),
    PrimitiveInfo("bytes_repeated", "bytes", "string", ['"hello"'], is_repeated=True),
    PrimitiveInfo("bytes_repeated_2", "bytes", "string", ['"hello"', '"world"'], is_repeated=True),
    PrimitiveInfo("bytes_repeated_0", "bytes", "string", [], is_repeated=True),

    PrimitiveInfo("bytes_long", "bytes", "string", ['"hello hello hello hello hello hello hello"']),
    PrimitiveInfo("bytes_very_long", "bytes", "string", ['"' + ("a" * 489) + '"']),
    PrimitiveInfo("bytes_15", "bytes", "string", ['"hello hello he"']),
]

varintvals = ["0ULL", "1ULL", "128ULL", "16384ULL", "2097152ULL", "268435456ULL", "34359738368ULL", "4398046511104ULL", "562949953421312ULL", "72057594037927936ULL", "9223372036854775808ULL"]

for testind in range(len(varintvals)):
    testval = varintvals[testind]
    primitive_info_objs.append(PrimitiveInfo("uint64_size" + str(testind).zfill(2) + "B", "uint64", "uint64_t", [testval for _ in range(gen_test_fields)]))
    primitive_info_objs.append(PrimitiveInfo("uint64_size" + str(testind).zfill(2) + "B" + "_repeated", "uint64", "uint64_t", [testval for _ in range(gen_test_fields)], is_repeated=True))

def proto_file_header():
    header = """
syntax = "proto2";

package primitivetests;

option cc_enable_arenas = true;

"""
    return header


def build_proto_file(alltestobjs):
    contents = proto_file_header()

    checkdups = set()

    for testobj in alltestobjs:
        protocontent = testobj.gen_simple_message()
        if protocontent not in checkdups:
            contents += protocontent
            checkdups.add(protocontent)

    writeme = open(protoout, "w")
    writeme.write(contents)
    writeme.close()



def cpp_file_header():
    header = """
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

"""
    return header

def cpp_test_contents(fieldtypeobj):
    contents = """

        std::cout << "s1\\n" << std::flush;

        #ifdef __riscv
        string hostplat = "riscv";

        AccelSetup();

        #else
        string hostplat = "x86";
        #endif

        #define ITERS 1000 // # of times to do accel
        //#define FAST // define this to just do acceleration + checking

        std::cout << "s2\\n" << std::flush;

        {TESTVALSINIT}


        std::cout << "s3\\n" << std::flush;

            google::protobuf::Arena arena;
            {ALLOCFIELDMESSAGE}

{SETTERLINES}

            string outstr;
            fillmessage->SerializeToString(&outstr);

            bool failcheck = false;

            std::cout << "encodedlen " << outstr.length() << "\\n" << std::flush;
            uint64_t total_bytes_processed = outstr.length() * ITERS;

            primitivetests::{MESSAGENAME}* parseintos[ITERS];

            for (int q = 0; q < ITERS; q++) {{
               parseintos[q] = google::protobuf::Arena::CreateMessage<primitivetests::{MESSAGENAME}>(&arena);
            }}

            string newstr[ITERS];
            for (int q = 0; q < ITERS; q++) {{
                newstr[q] = outstr;
            }}

    #ifdef __riscv
            std::cout << "s4\\n" << std::flush;

            auto t1 = std::chrono::steady_clock::now();

            asm volatile ("fence");

            for (int q = 0; q < ITERS; q++) {{
                AccelParseFromString(primitivetests, {MESSAGENAME}, parseintos[q], newstr[q]);
            }}

            block_on_completion();

            // Check if something went wrong
            if ({FENCECHECK}) {{
                failcheck = true;
            }}

            #ifndef FAST
            auto t2 = std::chrono::steady_clock::now();
            auto duration1 = std::chrono::duration_cast<std::chrono::microseconds>(t2 - t1).count();
            std::cout << (duration1 / (ITERS * 1.0)) << ", us per iter, " << hostplat << "-accel, {TESTNAME}, " << testvals[0] << "\\n" << std::flush;

            std::cout << (((double)(total_bytes_processed)) / (((double)duration1) / 1000000.0)  / 1000000000.0 * 8) << ", Gbits/s, " << hostplat << "-accel, {TESTNAME}, " << testvals[0] << "\\n" << std::flush;
            #endif

            if (failcheck) {{
                std::cout << "FAIL WRITE NOT IMMEDIATELY VISIBILE OR INCORRECT.\\n" << std::flush;
            }}

            for (int q = 0; q < ITERS; q++) {{
                {CHECK_EQUIV}
                if (!(parseintos[q]{HASCHECK})) {{
                    std::cout << "ACCEL FAILED hasbits ITER " << q << " ON {FIELDTYPE} TEST!\\n" << std::flush;
                    exit(1);
                }}
            }}
    #endif
            std::cout << "s5\\n" << std::flush;

            #ifndef FAST
            primitivetests::{MESSAGENAME}* parseintoscpu[ITERS];

            for (int q = 0; q < ITERS; q++) {{
               parseintoscpu[q] = google::protobuf::Arena::CreateMessage<primitivetests::{MESSAGENAME}>(&arena);
            }}

            auto t3 = std::chrono::steady_clock::now();
            for (int i = 0; i < ITERS; i++) {{
                parseintoscpu[i]->ParseFromString(newstr[i]);
            }}

            auto t4 = std::chrono::steady_clock::now();
            auto duration2 = std::chrono::duration_cast<std::chrono::microseconds>(t4 - t3).count();

            std::cout << (duration2 / (ITERS * 1.0)) << ", us per iter, " << hostplat << ", {TESTNAME}, " << testvals[0] << "\\n" << std::flush;

            std::cout << (((double)(total_bytes_processed)) / (((double)duration2) / 1000000.0)  / 1000000000.0 * 8) << ", Gbits/s, " << hostplat << ", {TESTNAME}, " << testvals[0] << "\\n" << std::flush;

            // only checks 1 elem (TODO: update)
            if (NUMTESTVALS_NON_MIN != 0 && fillmessage->{GETTER} != parseintoscpu[ITERS-1]->{GETTER}) {{
                printf("FAILED {FIELDTYPE} test.\\n");
                exit(1);
            }} else if (!(parseintoscpu[ITERS-1]{HASCHECK})) {{
                printf("FAILED hasbits for {FIELDTYPE} test.\\n");
                exit(1);
            }} else {{
                printf("PASSED {FIELDTYPE} test.\\n");
            }}
            #endif

        std::cout << "s6\\n" << std::flush;

        google::protobuf::ShutdownProtobufLibrary();
        return 0;
}}
""".format(
        CHECK_EQUIV=fieldtypeobj.get_check("parseintos[q]", "fillmessage", """std::cout << "ACCEL FAILED ITER " << q << " ON {FIELDTYPE} TEST!\\n" << std::flush;""".format(FIELDTYPE=fieldtypeobj.get_fieldname_single_ident())),
        FIELDTYPE=fieldtypeobj.get_fieldname_single_ident(),
        TESTVALSINIT=fieldtypeobj.generate_testvals_initialized(),
        SETTERLINES=fieldtypeobj.get_setter_lines(),
        GETTER=fieldtypeobj.get_getter(0, 0),
        FENCECHECK=fieldtypeobj.get_fencecheck(),
        ALLOCFIELDMESSAGE=fieldtypeobj.get_allocator_line(),
        TESTNAME=fieldtypeobj.get_test_name(),
        MESSAGENAME=fieldtypeobj.get_message_name(),
        HASCHECK=fieldtypeobj.get_hascheck(0))
    return contents

def build_cpp_tests(alltestobjs):
    for testobj in alltestobjs:
        contents = cpp_file_header()
        contents += cpp_test_contents(testobj)

        writeme = open(base_output_dir + testobj.get_test_name() + ".cpp", "w")
        writeme.write(contents)
        writeme.close()


def build_testsfrag(alltestobjs):
    rvtests = "rvtests = \\\n"
    x86tests = "x86tests = \\\n"

    for testobj in alltestobjs:
        rvtests += base_output_dir + testobj.get_test_name() +  ".riscv \\\n"
        x86tests += base_output_dir + testobj.get_test_name() + ".x86 \\\n"
    rvtests = rvtests[:-3]
    x86tests = x86tests[:-3]

    protocc = "protocc = " + protoout_cc
    protoh = "protoh = " + protoout_h
    protoboth = "protos = " + protoout_cc + " " + protoout_h

    contents = rvtests + "\n" + x86tests + "\n" + protocc + "\n" + protoh + "\n" + protoboth + "\n"

    writeme = open(testsfragout, "w")
    writeme.write(contents)
    writeme.close()



if __name__ == '__main__':
    base_output_dir = "primitive-tests/"
    protoout = base_output_dir + "primitives.proto"
    protoout_cc = base_output_dir + "primitives.pb.cc"
    protoout_h = base_output_dir + "primitives.pb.h"
    testsfragout= "testsfrag.mk"

    types_and_default_vals = dict()

    alltests = sorted(primitive_info_objs, key=lambda x: x.get_test_name())
    alltest_names = list(map(lambda x: x.get_test_name(), alltests))

    print("generating:")
    print(alltest_names)

    build_proto_file(alltests)
    build_cpp_tests(alltests)
    build_testsfrag(alltests)

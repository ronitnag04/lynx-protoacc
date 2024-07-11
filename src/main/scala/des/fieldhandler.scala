package protoacc.des

import chisel3._
import chisel3.util._

import org.chipsalliance.cde.config.{Parameters}

import freechips.rocketchip.util.DecoupledHelper

import protoacc._

class DescriptorResponseExtra extends Bundle {
  val unpacked_repeated = Bool()
  val is_repeatedptrfield = Bool()

  val ptr_to_repeatedfield = UInt(64.W)
  val ptr_to_repeatedfield_currentsizetotalsize = UInt(64.W)
  val ptr_to_repeatedfield_arenaorelements = UInt(64.W)

  val ptr_to_repeatedptrfield_currentsizecapacityproxy = UInt(64.W)
  val ptr_to_repeatedptrfield_taggedreporelem = UInt(64.W)
}

class FieldHandler()(implicit p: Parameters) extends Module {
  val io = IO(new Bundle {
    val consumer = Flipped(new MemLoaderConsumerBundle)

    val l1helperUser = new L1MemHelperBundle
    val l1helperUser2 = new L1MemHelperBundle

    val fixed_writer_request = Decoupled(new FixedWriterRequest)

    val fixed_alloc_region_addr = Flipped(Valid(UInt(64.W)))
    val array_alloc_region_addr = Flipped(Valid(UInt(64.W)))

    val completed_toplevel_bufs = Output(UInt(64.W))
  })

  val completed_toplevel_bufs_reg = RegInit(0.U(64.W))
  io.completed_toplevel_bufs := completed_toplevel_bufs_reg

  val just_completed_buffer = RegInit(false.B)
  val last_consumer_transaction = (io.consumer.available_output_bytes === io.consumer.user_consumed_bytes) && io.consumer.output_last_chunk

  when (io.consumer.output_ready && io.consumer.output_valid) {
    when (last_consumer_transaction) {
      just_completed_buffer := true.B
      val next_completed_toplevel_bufs_reg = completed_toplevel_bufs_reg + 1.U
      completed_toplevel_bufs_reg := next_completed_toplevel_bufs_reg
      ProtoaccLogger.logInfo("completed bufs: current 0x%x, next 0x%x\n",
        completed_toplevel_bufs_reg, next_completed_toplevel_bufs_reg)
    }
  }

  val processed_len_total = RegInit(0.U(64.W))

  val stacks_index = RegInit(0.U(ProtoaccParams.MAX_NESTED_LEVELS_WIDTH.W))
  assert(stacks_index < ProtoaccParams.MAX_NESTED_LEVELS.U, "FAIL. TOO MANY NESTED LEVELS")

  val out_addr_stack = Reg(Vec(ProtoaccParams.MAX_NESTED_LEVELS, UInt(64.W)))
  val hasbits_offset_stack = Reg(Vec(ProtoaccParams.MAX_NESTED_LEVELS, UInt(64.W)))
  val descr_table_stack = Reg(Vec(ProtoaccParams.MAX_NESTED_LEVELS, UInt(64.W)))
  val lens_table_stack = RegInit(VecInit(Seq.fill(ProtoaccParams.MAX_NESTED_LEVELS)(0.U(64.W))))
  val min_field_no_stack = RegInit(VecInit(Seq.fill(ProtoaccParams.MAX_NESTED_LEVELS)(0.U(32.W))))

  val default_hasbits_val = (0x10).U(64.W)

  val current_out_addr = Mux(stacks_index === 0.U,
    io.consumer.output_decoded_dest_base_addr,
    out_addr_stack(stacks_index))
  val current_hasbits_offset = Mux(stacks_index === 0.U,
    default_hasbits_val,
    hasbits_offset_stack(stacks_index))
  val current_descr_table = Mux(stacks_index === 0.U,
    io.consumer.output_ADT_addr,
    descr_table_stack(stacks_index))
  val current_min_field_no = Mux(stacks_index === 0.U,
    io.consumer.output_min_field_no,
    min_field_no_stack(stacks_index))
  val current_len = lens_table_stack(stacks_index)



  when (io.consumer.output_ready && io.consumer.output_valid) {
    when (last_consumer_transaction) {
      ProtoaccLogger.logInfo("NStack: Clearing\n")
      stacks_index := 0.U
      processed_len_total := 0.U
    } .otherwise {

      val next_processed_len_total = processed_len_total + io.consumer.user_consumed_bytes
      when (next_processed_len_total === current_len) {
        ProtoaccLogger.logInfo("NStack: Removing an entry.\n")
        stacks_index := stacks_index - 1.U
      }
      processed_len_total := next_processed_len_total
    }
  }


  val descriptor_table_address_user = current_descr_table
  val output_address_user = current_out_addr


  val combo_varint_module = Module(new CombinationalVarint)
  combo_varint_module.io.inputRawData := io.consumer.output_data
  val varintlen = Wire(UInt())
  varintlen := combo_varint_module.io.consumedLenBytes
  val varintresult = Wire(UInt())
  varintresult := combo_varint_module.io.outputData


  when (io.consumer.output_ready && io.consumer.output_valid) {
    ProtoaccLogger.logInfo("RAW DATA: %x, OUTPUT BYTES AVAIL: %x, DATA AS VARINT %x, VARINT LEN %x, BYTES CONSUMED %x\n",
      io.consumer.output_data,
      io.consumer.available_output_bytes,
      varintresult,
      varintlen,
      io.consumer.user_consumed_bytes)
  }


  val NUM_BITS_FOR_STATES = 4
  val sHandleVarint = 0.U(NUM_BITS_FOR_STATES.W)
  val sHandle64bit  = 1.U(NUM_BITS_FOR_STATES.W)
  val sHandleLengthDelim  = 2.U(NUM_BITS_FOR_STATES.W)
  val sHandleStartGroup  = 3.U(NUM_BITS_FOR_STATES.W)
  val sHandleEndGroup  = 4.U(NUM_BITS_FOR_STATES.W)
  val sHandle32bit  = 5.U(NUM_BITS_FOR_STATES.W)

  val sReadKey = 6.U(NUM_BITS_FOR_STATES.W)
  val sPrepState = 7.U(NUM_BITS_FOR_STATES.W)
  val sManageRepeatedAlloc = 8.U(NUM_BITS_FOR_STATES.W)
  val sEndBufCloseurcArray = 9.U(NUM_BITS_FOR_STATES.W)


  val fieldState = RegInit(sReadKey)
  val wireTypeReg = RegInit(0.U(NUM_BITS_FOR_STATES.W))
  val descr_resp_reg = Reg(new DescriptorResponse)
  val descr_resp_extra_reg = Reg(new DescriptorResponseExtra)

  io.consumer.user_consumed_bytes := 0.U
  io.consumer.output_ready := false.B

  val field_no_reg = RegInit(0.U(32.W))

  val descriptor_table_handler = Module(new DescriptorTableHandler)

  descriptor_table_handler.io.extra_meta_response.ready := false.B

  io.l1helperUser <> descriptor_table_handler.io.l1helperUser

  val wire_type = varintresult & 7.U
  val field_no = varintresult >> 3

  descriptor_table_handler.io.field_dest_request.bits.proto_addr := output_address_user
  descriptor_table_handler.io.field_dest_request.bits.relative_field_no := field_no - current_min_field_no
  descriptor_table_handler.io.field_dest_request.bits.base_info_ptr := descriptor_table_address_user
  descriptor_table_handler.io.field_dest_request.valid := false.B
  descriptor_table_handler.io.field_dest_response.ready := false.B

  io.fixed_writer_request.valid := false.B
  io.fixed_writer_request.bits.write_addr := descr_resp_reg.write_addr
  io.fixed_writer_request.bits.write_width := 0.U
  io.fixed_writer_request.bits.write_data := 0.U

  def set_fixed_write(addr: UInt, width: UInt, data: UInt): Unit = {
    io.fixed_writer_request.valid := true.B
    io.fixed_writer_request.bits.write_addr := addr
    io.fixed_writer_request.bits.write_width := width
    io.fixed_writer_request.bits.write_data := data
  }

  val type_info =   descr_resp_reg.proto_field_type
  val is_repeated = descr_resp_reg.is_repeated

  val type_varint64 = (type_info === PROTO_TYPES.TYPE_INT64) || (type_info === PROTO_TYPES.TYPE_UINT64) || (type_info === PROTO_TYPES.TYPE_SINT64)
  val type_bool = (type_info === PROTO_TYPES.TYPE_BOOL)
  val type_need_zigzag64 = (type_info === PROTO_TYPES.TYPE_SINT64)
  val type_need_zigzag32 = (type_info === PROTO_TYPES.TYPE_SINT32)

  val varint_zigzag32_result = (varintresult(31, 0) >> 1) ^ ((~(varintresult(31, 0) & 1.U)) + 1.U)
  val varint_zigzag64_result = (varintresult >> 1) ^ ((~(varintresult & 1.U)) + 1.U)

  val fixed_alloc_region_next = RegInit(0.U(64.W))
  val initial_fixed_alloc_region = RegInit(0.U(64.W))
  when (io.fixed_alloc_region_addr.valid) {
    ProtoaccLogger.logInfo("Obtained {initial_}fixed_alloc_region{_next}: 0x%x\n", io.fixed_alloc_region_addr.bits)
    fixed_alloc_region_next := io.fixed_alloc_region_addr.bits
    initial_fixed_alloc_region := io.fixed_alloc_region_addr.bits
  }
  assert((fixed_alloc_region_next & 7.U) === 0.U, "Fixed alloc region ptr must be 8-byte aligned\n")

  val array_alloc_region_next = RegInit(0.U(64.W))
  when (io.array_alloc_region_addr.valid) {
    ProtoaccLogger.logInfo("Obtained array_alloc_region_next: 0x%x\n", io.array_alloc_region_addr.bits)
    array_alloc_region_next := io.array_alloc_region_addr.bits
  }
  assert((array_alloc_region_next & 7.U) === 0.U, "Array alloc region ptr must be 8-byte aligned\n")

  val sStringWait = 0.U
  val sStringReadLength = 1.U
  val sStringWriteHeader0 = 2.U
  val sStringWriteHeader1 = 3.U
  val sStringMoveData = 4.U
  val sStringDone = 5.U
  val stringFieldState = RegInit(sStringWait)

  val sPackedRepeatedWait = 0.U
  val sPackedRepeatedReadByteLength = 1.U
  val sPackedRepeatedMoveData = 2.U
  val sPackedRepeatedWriteHeader = 3.U
  val packedRepeatedFieldState = RegInit(sPackedRepeatedWait)

  val sNestedMessageWait = 0.U
  val sGetDescrTableAddr = 1.U
  val sLoadVPtr = 2.U
  val sObjLenStackManagement = 3.U
  val switchNestedMessageSetupState = RegInit(sNestedMessageWait)


  val urc_valid = RegInit(false.B)

  val urc_ptr_to_repeatedfield = RegInit(0.U(64.W))
  val urc_is_repeatedptrfield = RegInit(false.B)

  val urc_ptr_to_inobjsizes = RegInit(0.U(64.W))

  val urc_ptr_to_repallocsize = RegInit(0.U(64.W))

  val urc_next_write_addr = RegInit(0.U(64.W))
  val urc_elems_written = RegInit(0.U(64.W))

  val urc_teardown_stage = RegInit(0.U(2.W))

  val hasbitswriter = Module(new HasBitsWriter)
  io.l1helperUser2 <> hasbitswriter.io.l1helperUser
  hasbitswriter.io.requestin.valid := false.B
  hasbitswriter.io.requestin.bits.flushonly := false.B
  hasbitswriter.io.requestin.bits := DontCare

  val fire_sReadKey = DecoupledHelper(
    descriptor_table_handler.io.field_dest_request.ready,
    io.consumer.output_valid,
    hasbitswriter.io.requestin.ready
  )

  // MISC HELPERS

  def add_aligned(in: UInt, bytes_aligned_to: Int): UInt = {
    require(isPow2(bytes_aligned_to))
    ((in +& (bytes_aligned_to - 1).U) >> log2Up(bytes_aligned_to).U) << log2Up(bytes_aligned_to).U
  }

  def create_currentsize_capacityproxy(in: UInt): UInt = {
    // write current_size_ (lower bits) and capacity_proxy_ (upper bits)
    val kSSOCapacity = 1 // const used to create capacity_proxy
    ((Mux(in > 0.U, in - kSSOCapacity.U, 0.U) << 32) | in(31, 0))(63, 0)
  }

  def create_currentsize_totalsize(in: UInt): UInt = {
    // write current_size_ (lower bits) and total_size_ (upper bits)
    ((in << 32) | in(31, 0))(63, 0)
  }

  switch (fieldState) {
    is (sReadKey) {
      io.consumer.user_consumed_bytes := varintlen
      io.consumer.output_ready := fire_sReadKey.fire(io.consumer.output_valid)
      descriptor_table_handler.io.field_dest_request.valid := fire_sReadKey.fire(descriptor_table_handler.io.field_dest_request.ready)
      hasbitswriter.io.requestin.valid := fire_sReadKey.fire(hasbitswriter.io.requestin.ready)

      hasbitswriter.io.requestin.bits.hasbits_base_addr := current_out_addr + current_hasbits_offset
      hasbitswriter.io.requestin.bits.relative_fieldno := field_no - current_min_field_no
      hasbitswriter.io.requestin.bits.flushonly := false.B

      when (fire_sReadKey.fire()) {
        ProtoaccLogger.logInfo("sReadKey: fieldno: %d, wire_type: %d\n", field_no, wire_type)
        fieldState := sPrepState
        field_no_reg := field_no
        wireTypeReg := wire_type
        just_completed_buffer := false.B
      } .elsewhen (just_completed_buffer) {
        ProtoaccLogger.logInfo("sReadKey: just_completed_buffer=1 write to hasbitswriter\n")
        hasbitswriter.io.requestin.valid := true.B
        hasbitswriter.io.requestin.bits.flushonly := true.B
        when (hasbitswriter.io.requestin.ready) {
          just_completed_buffer := false.B
        }
      }
    }

    is (sPrepState) {
      // wait for a descriptor table response
      descriptor_table_handler.io.field_dest_response.ready := true.B

      // receive descriptor table response
      when (descriptor_table_handler.io.field_dest_response.valid) {
        ProtoaccLogger.logInfo("sPrepState: got descriptor table response\n")

        val descr_resp_bits = descriptor_table_handler.io.field_dest_response.bits
        descr_resp_reg := descr_resp_bits

        val is_str_bytes_msg = Seq(PROTO_TYPES.TYPE_STRING, PROTO_TYPES.TYPE_BYTES, PROTO_TYPES.TYPE_MESSAGE).map(descr_resp_bits.proto_field_type === _).reduce(_ || _)

        val unpacked_repeated = descr_resp_bits.is_repeated && (wireTypeReg =/= sHandleLengthDelim || is_str_bytes_msg)

        descr_resp_extra_reg.unpacked_repeated := unpacked_repeated
        descr_resp_extra_reg.is_repeatedptrfield := descr_resp_bits.is_repeated && is_str_bytes_msg

        // the address given here is +8 into the repeated object (see the serializer field handler)

        // same as in old code
        descr_resp_extra_reg.ptr_to_repeatedfield := descr_resp_bits.write_addr - 8.U // ptr to the RepeatedField obj
        descr_resp_extra_reg.ptr_to_repeatedfield_currentsizetotalsize := descr_resp_bits.write_addr - 8.U // ptr to current_size_ and total_size_
        descr_resp_extra_reg.ptr_to_repeatedfield_arenaorelements := descr_resp_bits.write_addr // ptr to struct Rep

        // new changes
        descr_resp_extra_reg.ptr_to_repeatedptrfield_currentsizecapacityproxy := descr_resp_bits.write_addr // ptr to current_size_ and capacity_proxy_ in RepeatedPtrFieldBase
        descr_resp_extra_reg.ptr_to_repeatedptrfield_taggedreporelem := descr_resp_bits.write_addr - 8.U // ptr to tagged_rep_or_elem

        when (unpacked_repeated) {
          fieldState := sManageRepeatedAlloc
        } .otherwise {
          fieldState := wireTypeReg
        }
      }
    }

    is (sManageRepeatedAlloc) {
      ProtoaccLogger.logInfo("fieldState: sManageRepeatedAlloc\n")

      when (urc_valid) {
        ProtoaccLogger.logInfo("urc_valid in sManageRepeatedAlloc: urc_ptr:0x%x desc_resp_reg:0x%x\n",
          urc_ptr_to_repeatedfield, descr_resp_extra_reg.ptr_to_repeatedfield)
        when (urc_ptr_to_repeatedfield === descr_resp_extra_reg.ptr_to_repeatedfield) {
          descr_resp_reg.write_addr := urc_next_write_addr
          fieldState := wireTypeReg
          ProtoaccLogger.logInfo("[unpacked repeat] continuing. waddr will be: 0x%x, elems written: 0x%x\n",
            urc_next_write_addr,
            urc_elems_written)
        } .otherwise {
          when (urc_is_repeatedptrfield) {
            when (urc_teardown_stage === 0.U) {
              set_fixed_write(urc_ptr_to_inobjsizes, 3.U, create_currentsize_totalsize(urc_elems_written))

              when (io.fixed_writer_request.ready) {
                ProtoaccLogger.logInfo("[unpacked repptrfield] closeout s0\n")
                urc_teardown_stage := 1.U
              }
            } .otherwise {
              set_fixed_write(urc_ptr_to_repallocsize, 3.U, urc_elems_written)

              when (io.fixed_writer_request.ready) {
                ProtoaccLogger.logInfo("[unpacked repptrfield] closeout s1\n")
                urc_valid := false.B
                array_alloc_region_next := add_aligned(urc_next_write_addr, 8)
                urc_teardown_stage := 0.U
              }
            }
          } .otherwise {
            set_fixed_write(urc_ptr_to_inobjsizes, 3.U, create_currentsize_totalsize(urc_elems_written))

            when (io.fixed_writer_request.ready) {
              ProtoaccLogger.logInfo("[unpacked repfield] closeout\n")

              urc_valid := false.B
              array_alloc_region_next := add_aligned(urc_next_write_addr, 8)
            }
          }
        }
      } .otherwise {
        ProtoaccLogger.logInfo("urc_valid is invalid in sManageRepeatedAlloc\n")
        when (descr_resp_extra_reg.is_repeatedptrfield) {
          // this is writing to the tagged_rep_or_elem_ member of the RepeatedPtrFieldBase class
          // if sz=1 or 0 then ptr's 0th bit is 0, otherwise it's 1 (when it should either be a rep pointer (if sz >= 2) or sz=1 std::string ptr or sz=0 null ptr)
          // for now, assume that it is a rep object instead of doing the tagging (this will be overridden later)
          set_fixed_write(descr_resp_extra_reg.ptr_to_repeatedptrfield_taggedreporelem, 3.U, array_alloc_region_next | 1.U) // tag with 1 since it is a rep (not used for mem access)

          when (io.fixed_writer_request.ready) {
            urc_next_write_addr := array_alloc_region_next + 8.U // ptr to Rep objects elements ptr
            urc_valid := true.B

            descr_resp_reg.write_addr := array_alloc_region_next + 8.U // ptr to Rep object's elements member

            ProtoaccLogger.logInfo("[unpacked repptrfield] starting s0. putting a Rep obj at 0x%x\n",
              array_alloc_region_next)

            urc_ptr_to_repeatedfield := descr_resp_extra_reg.ptr_to_repeatedfield
            urc_is_repeatedptrfield := true.B
            urc_ptr_to_inobjsizes := descr_resp_extra_reg.ptr_to_repeatedptrfield_currentsizecapacityproxy
            urc_ptr_to_repallocsize := array_alloc_region_next // ptr to Rep object's allocated_size

            urc_elems_written := 0.U
            urc_teardown_stage := 0.U

            // in this case just to something that handles a string/bytes
            fieldState := wireTypeReg
          }
        } .otherwise {
          // write elem of RepeatedField
          set_fixed_write(descr_resp_extra_reg.ptr_to_repeatedfield_arenaorelements, 3.U, array_alloc_region_next)

          when (io.fixed_writer_request.ready) {
            descr_resp_reg.write_addr := array_alloc_region_next

            urc_valid := true.B
            urc_ptr_to_repeatedfield := descr_resp_extra_reg.ptr_to_repeatedfield
            urc_is_repeatedptrfield := false.B
            urc_ptr_to_inobjsizes := descr_resp_extra_reg.ptr_to_repeatedfield_currentsizetotalsize
            urc_ptr_to_repallocsize := 0.U // this Rep obj doesn't have any allocated size field

            urc_next_write_addr := array_alloc_region_next
            urc_elems_written := 0.U
            urc_teardown_stage := 0.U
            ProtoaccLogger.logInfo("[unpacked repfield] starting. waddr will be 0x%x\n",
              array_alloc_region_next)

            // jump to something that handles any primitive type
            fieldState := wireTypeReg
          }
        }
      }
    }

    is (sHandleVarint) {
      ProtoaccLogger.logInfo("fieldState: sHandleVarint\n")
      io.consumer.user_consumed_bytes := varintlen
      io.consumer.output_ready := io.fixed_writer_request.ready
      io.fixed_writer_request.valid := io.consumer.output_valid

      io.fixed_writer_request.bits.write_width := Mux(type_varint64,
                                                    3.U,
                                                    Mux(type_bool,
                                                      0.U,
                                                      2.U))

      when (type_need_zigzag64) {
        io.fixed_writer_request.bits.write_data := varint_zigzag64_result
      } .elsewhen (type_need_zigzag32) {
        io.fixed_writer_request.bits.write_data := varint_zigzag32_result
      } .otherwise {
        io.fixed_writer_request.bits.write_data := varintresult
      }

      when (io.consumer.output_valid && io.fixed_writer_request.ready) {
        when (descr_resp_reg.is_repeated) {
          urc_next_write_addr := urc_next_write_addr + (1.U(64.W) << io.fixed_writer_request.bits.write_width)
          urc_elems_written := urc_elems_written + 1.U
        }
        ProtoaccLogger.logInfo("Handle Varint. fieldno: %d, value: 0x%x\n", field_no_reg, varintresult)
        ProtoaccLogger.logInfo("Handle Varint. is_repeated: %d, type: %d, waddr: 0x%x\n",
          is_repeated, type_info, io.fixed_writer_request.bits.write_addr)
        when (urc_valid && last_consumer_transaction) {
          fieldState := sEndBufCloseurcArray
        } .otherwise {
          fieldState := sReadKey
        }
      }
    }

    is (sHandle64bit) {
      io.consumer.user_consumed_bytes := 8.U
      io.consumer.output_ready := io.fixed_writer_request.ready
      io.fixed_writer_request.bits.write_width := 3.U


      val result64 = io.consumer.output_data(63, 0)
      io.fixed_writer_request.bits.write_data := result64
      io.fixed_writer_request.valid := io.consumer.output_valid

      when (io.consumer.output_valid && io.fixed_writer_request.ready) {
        when (descr_resp_reg.is_repeated) {
          urc_next_write_addr := urc_next_write_addr + (1.U(64.W) << io.fixed_writer_request.bits.write_width)
          urc_elems_written := urc_elems_written + 1.U
        }

        ProtoaccLogger.logInfo("Handle 64bit. fieldno: %d, value: 0x%x\n", field_no_reg, result64)
        ProtoaccLogger.logInfo("Handle 64bit. is_repeated: %d, type: %d, waddr: 0x%x\n",
          is_repeated, type_info, io.fixed_writer_request.bits.write_addr)

        when (urc_valid && last_consumer_transaction) {
          fieldState := sEndBufCloseurcArray
        } .otherwise {
          fieldState := sReadKey
        }

      }
    }

    is (sHandle32bit) {
      io.consumer.user_consumed_bytes := 4.U
      io.consumer.output_ready := io.fixed_writer_request.ready
      io.fixed_writer_request.bits.write_width := 2.U
      val result32 = io.consumer.output_data(31, 0)
      io.fixed_writer_request.bits.write_data := result32
      io.fixed_writer_request.valid := io.consumer.output_valid

      when (io.consumer.output_valid && io.fixed_writer_request.ready) {
        when (descr_resp_reg.is_repeated) {
          urc_next_write_addr := urc_next_write_addr + (1.U(64.W) << io.fixed_writer_request.bits.write_width)
          urc_elems_written := urc_elems_written + 1.U
        }

        ProtoaccLogger.logInfo("Handle 32bit. fieldno: %d, value: 0x%x\n", field_no_reg, result32)
        ProtoaccLogger.logInfo("Handle 32bit. is_repeated: %d, type: %d, waddr: 0x%x\n",
          is_repeated, type_info, io.fixed_writer_request.bits.write_addr)

        when (urc_valid && last_consumer_transaction) {
          fieldState := sEndBufCloseurcArray
        } .otherwise {
          fieldState := sReadKey
        }

      }
    }

    is (sHandleLengthDelim) {
      val nested_message = type_info === PROTO_TYPES.TYPE_MESSAGE
      val string_or_bytes = ((type_info === PROTO_TYPES.TYPE_STRING) || (type_info === PROTO_TYPES.TYPE_BYTES))
      val packed_repeated = !nested_message && !string_or_bytes

      val nestedobj_encodedlen = RegInit(0.U(64.W))
      val newobjwriteaddr = RegInit(0.U(64.W))
      val newobj_descriptor = RegInit(0.U(64.W))
      val newobj_vptr = RegInit(0.U(64.W))

      // these must be mutually exclusive so that inner state machines don't overlap
      assert(Seq(nested_message, packed_repeated, string_or_bytes).map(_.asUInt).reduce(_ +& _) <= 1.U, "Ensure state machines don't overlap")

      switch (switchNestedMessageSetupState) {
        is (sNestedMessageWait) {
          when (nested_message) {
            io.fixed_writer_request.bits.write_data := fixed_alloc_region_next
            io.fixed_writer_request.bits.write_width := 3.U
            io.consumer.output_ready := io.fixed_writer_request.ready
            io.fixed_writer_request.valid := io.consumer.output_valid
            io.consumer.user_consumed_bytes := varintlen
          }

          when (io.consumer.output_valid && io.fixed_writer_request.ready && nested_message) {
            when (descr_resp_reg.is_repeated) {
              urc_next_write_addr := urc_next_write_addr + (1.U(64.W) << io.fixed_writer_request.bits.write_width)
              urc_elems_written := urc_elems_written + 1.U
            }

            ProtoaccLogger.logInfo("NESTED MESSAGE. s1. is_repeated: %d, type: %d, ptr_waddr: 0x%x, ptr_value: 0x%x, packedfieldlen: %d bytes\n",
              is_repeated, type_info, io.fixed_writer_request.bits.write_addr,
              fixed_alloc_region_next, varintresult)

            nestedobj_encodedlen := varintresult
            switchNestedMessageSetupState := sGetDescrTableAddr

            newobjwriteaddr := fixed_alloc_region_next
          }
        }

        is (sGetDescrTableAddr) {
          descriptor_table_handler.io.extra_meta_response.ready := true.B
          when (descriptor_table_handler.io.extra_meta_response.valid) {
            newobj_descriptor := descriptor_table_handler.io.extra_meta_response.bits.extra_meta0
            switchNestedMessageSetupState := sLoadVPtr
          }
        }

        is (sLoadVPtr) {
          descriptor_table_handler.io.extra_meta_response.ready := io.fixed_writer_request.ready

          val obtained_vptr = descriptor_table_handler.io.extra_meta_response.bits.extra_meta0

          set_fixed_write(newobjwriteaddr, 3.U, obtained_vptr)

          when (descriptor_table_handler.io.extra_meta_response.valid && io.fixed_writer_request.ready) {
            newobj_vptr := obtained_vptr
            switchNestedMessageSetupState := sObjLenStackManagement

            val newobj_cpp_len = descriptor_table_handler.io.extra_meta_response.bits.extra_meta1
            val newobj_cpp_len_align8 = ((newobj_cpp_len + 7.U) >> 3.U) << 3.U
            fixed_alloc_region_next := fixed_alloc_region_next + newobj_cpp_len_align8
          }
        }

        is (sObjLenStackManagement) {
          descriptor_table_handler.io.extra_meta_response.ready := true.B
          when (descriptor_table_handler.io.extra_meta_response.valid) {
            switchNestedMessageSetupState := sNestedMessageWait

            when (urc_valid && just_completed_buffer) {
              fieldState := sEndBufCloseurcArray
            } .otherwise {
              fieldState := sReadKey
            }

            val min_max_field_nos = descriptor_table_handler.io.extra_meta_response.bits.extra_meta1
            val obtained_hasbits_offset = descriptor_table_handler.io.extra_meta_response.bits.extra_meta0
            val min_field_no = min_max_field_nos >> 32
            val max_field_no = min_max_field_nos(31, 0)
            ProtoaccLogger.logInfo("MinFieldNo: %d, MaxFieldNo: %d", min_field_no, max_field_no)

            val compare_encoded_lens = processed_len_total + nestedobj_encodedlen
            when (compare_encoded_lens === current_len) {
              ProtoaccLogger.logInfo("NStack: Replacing top entry\n")
              out_addr_stack(stacks_index) := newobjwriteaddr
              hasbits_offset_stack(stacks_index) := obtained_hasbits_offset
              descr_table_stack(stacks_index) := newobj_descriptor
              min_field_no_stack(stacks_index) := min_field_no
            } .otherwise {
              ProtoaccLogger.logInfo("NStack: Adding entry\n")
              val next_stack_ind = stacks_index + 1.U
              out_addr_stack(next_stack_ind) := newobjwriteaddr
              hasbits_offset_stack(next_stack_ind) := obtained_hasbits_offset
              descr_table_stack(next_stack_ind) := newobj_descriptor
              lens_table_stack(next_stack_ind) := compare_encoded_lens
              min_field_no_stack(next_stack_ind) := min_field_no
              stacks_index := next_stack_ind
            }
          }
        }
      }

      val repLenBytesLeft = RegInit(0.U(64.W))
      val type_tracker = RegInit(0.U(64.W))
      val repeatedfield_obj_addr = RegInit(0.U(64.W))
      val current_packed_write_ptr = RegInit(0.U(64.W))
      val elements_written = RegInit(0.U(64.W))

      switch (packedRepeatedFieldState) {
        is (sPackedRepeatedWait) {
          when (packed_repeated) {
            io.fixed_writer_request.bits.write_data := fixed_alloc_region_next
            io.fixed_writer_request.bits.write_width := 3.U
            io.consumer.output_ready := io.fixed_writer_request.ready
            io.fixed_writer_request.valid := io.consumer.output_valid
            io.consumer.user_consumed_bytes := varintlen
          }

          when (io.fixed_writer_request.ready && io.consumer.output_valid && packed_repeated) {
            type_tracker := type_info
            repeatedfield_obj_addr := descr_resp_reg.write_addr

            repLenBytesLeft := varintresult
            elements_written := 0.U

            packedRepeatedFieldState := sPackedRepeatedMoveData

            ProtoaccLogger.logInfo("PACKED_REPEATED. s1. is_repeated: %d, type: %d, ptr_waddr: 0x%x, ptr_value: 0x%x, packedfieldlen: %d bytes\n",
              is_repeated, type_info, io.fixed_writer_request.bits.write_addr,
              fixed_alloc_region_next, varintresult)

            current_packed_write_ptr := fixed_alloc_region_next
          }
        }

        is (sPackedRepeatedMoveData) {
          io.fixed_writer_request.bits.write_addr := current_packed_write_ptr

          val consume_varint = type_tracker === PROTO_TYPES.TYPE_INT64 ||
                              type_tracker === PROTO_TYPES.TYPE_UINT64 ||
                              type_tracker === PROTO_TYPES.TYPE_INT32 ||
                              type_tracker === PROTO_TYPES.TYPE_BOOL ||
                              type_tracker === PROTO_TYPES.TYPE_UINT32 ||
                              type_tracker === PROTO_TYPES.TYPE_ENUM ||
                              type_tracker === PROTO_TYPES.TYPE_SINT32 ||
                              type_tracker === PROTO_TYPES.TYPE_SINT64
          val consume_8bytes = type_tracker === PROTO_TYPES.TYPE_DOUBLE ||
                               type_tracker === PROTO_TYPES.TYPE_FIXED64
          val consume_4bytes = type_tracker === PROTO_TYPES.TYPE_FLOAT ||
                               type_tracker === PROTO_TYPES.TYPE_FIXED32

          val consume_width = Wire(UInt(4.W))
          consume_width := 0.U
          when (consume_varint) {
            io.consumer.user_consumed_bytes := varintlen
            consume_width := varintlen
          } .elsewhen (consume_8bytes) {
            io.consumer.user_consumed_bytes := 8.U
            consume_width := 8.U
          } .elsewhen (consume_4bytes) {
            io.consumer.user_consumed_bytes := 4.U
            consume_width := 4.U
          } .otherwise {
            assert(false.B, "ERROR")
          }

          val write_8bytes = type_tracker === PROTO_TYPES.TYPE_DOUBLE ||
                             type_tracker === PROTO_TYPES.TYPE_FIXED64 ||
                             type_tracker === PROTO_TYPES.TYPE_INT64 ||
                             type_tracker === PROTO_TYPES.TYPE_UINT64 ||
                             type_tracker === PROTO_TYPES.TYPE_SINT64
          val write_4bytes = type_tracker === PROTO_TYPES.TYPE_FLOAT ||
                             type_tracker === PROTO_TYPES.TYPE_FIXED32 ||
                             type_tracker === PROTO_TYPES.TYPE_INT32 ||
                             type_tracker === PROTO_TYPES.TYPE_UINT32 ||
                             type_tracker === PROTO_TYPES.TYPE_SINT32 ||
                             type_tracker === PROTO_TYPES.TYPE_ENUM
          val write_1bytes = type_tracker === PROTO_TYPES.TYPE_BOOL


          val write_width = Wire(UInt(4.W))
          write_width := 0.U
          when (write_8bytes) {
            io.fixed_writer_request.bits.write_width := 3.U
            write_width := 8.U
          } .elsewhen (write_4bytes) {
            io.fixed_writer_request.bits.write_width := 2.U
            write_width := 4.U
          } .elsewhen (write_1bytes) {
            io.fixed_writer_request.bits.write_width := 0.U
            write_width := 1.U
          } .otherwise {
            assert(false.B, "ERROR")
          }


          val output_signed_varint64 = type_tracker === PROTO_TYPES.TYPE_SINT64
          val output_signed_varint32 = type_tracker === PROTO_TYPES.TYPE_SINT32
          val output_regular_varint = type_tracker === PROTO_TYPES.TYPE_INT64 ||
                                      type_tracker === PROTO_TYPES.TYPE_UINT32 ||
                                      type_tracker === PROTO_TYPES.TYPE_UINT64 ||
                                      type_tracker === PROTO_TYPES.TYPE_INT32 ||
                                      type_tracker === PROTO_TYPES.TYPE_BOOL ||
                                      type_tracker === PROTO_TYPES.TYPE_ENUM
          val output_raw = type_tracker === PROTO_TYPES.TYPE_DOUBLE ||
                           type_tracker === PROTO_TYPES.TYPE_FLOAT ||
                           type_tracker === PROTO_TYPES.TYPE_FIXED64 ||
                           type_tracker === PROTO_TYPES.TYPE_FIXED32

          when (output_signed_varint64) {
            io.fixed_writer_request.bits.write_data := varint_zigzag64_result
          } .elsewhen (output_signed_varint32) {
            io.fixed_writer_request.bits.write_data := varint_zigzag32_result
          } .elsewhen (output_regular_varint) {
            io.fixed_writer_request.bits.write_data := varintresult
          } .elsewhen (output_raw) {
            io.fixed_writer_request.bits.write_data := io.consumer.output_data(63, 0)
          } .otherwise {
            assert(false.B, "ERROR")
          }

          io.fixed_writer_request.valid := io.consumer.output_valid
          io.consumer.output_ready := io.fixed_writer_request.ready
          when (io.fixed_writer_request.ready && io.consumer.output_valid) {
            ProtoaccLogger.logInfo("PACKED_REPEATED. s2. write_width %d, consumed_bytes %d\n", write_width, consume_width)

            repLenBytesLeft := repLenBytesLeft - consume_width
            current_packed_write_ptr := current_packed_write_ptr + write_width
            elements_written := elements_written + 1.U

            when (repLenBytesLeft === consume_width) {
              ProtoaccLogger.logInfo("PACKED_REPEATED. s3. write_width %d, consumed_bytes %d\n", write_width, consume_width)
              repeatedfield_obj_addr := repeatedfield_obj_addr - 8.U
              fixed_alloc_region_next := ((current_packed_write_ptr + write_width + 7.U) >> 3.U) << 3.U
              packedRepeatedFieldState := sPackedRepeatedWriteHeader
            }
          }
        }

        is (sPackedRepeatedWriteHeader) {
          set_fixed_write(repeatedfield_obj_addr, 3.U, create_currentsize_totalsize(elements_written))

          when(io.fixed_writer_request.ready) {
              ProtoaccLogger.logInfo("PACKED_REPEATED. s4. sizes 0x%x, addr 0x%x\n",
              io.fixed_writer_request.bits.write_data, io.fixed_writer_request.bits.write_addr)
            packedRepeatedFieldState := sPackedRepeatedWait

            when (urc_valid && just_completed_buffer) {
              fieldState := sEndBufCloseurcArray
            } .otherwise {
              fieldState := sReadKey
            }
          }
        }
      }

      val stringLenNoNull = RegInit(0.U(64.W))
      val stringLenWithNull = RegInit(0.U(64.W))
      val stringLenWithNullPadded = RegInit(0.U(64.W))
      val data_write_ptr = RegInit(0.U(64.W))

      val obj_header_write_ptr = RegInit(0.U(64.W))
      val handling_bytes = RegInit(false.B)

      switch (stringFieldState) {
        is (sStringWait) {

          val fixed_alloc_region_next_16B_aligned = add_aligned(fixed_alloc_region_next, 16)

          when (string_or_bytes) {
            ProtoaccLogger.logInfo("stringFieldState: sStringWait\n")
            io.fixed_writer_request.bits.write_data := fixed_alloc_region_next_16B_aligned
            io.fixed_writer_request.bits.write_width := 3.U
            io.consumer.output_ready := io.fixed_writer_request.ready
            io.fixed_writer_request.valid := io.consumer.output_valid
            io.consumer.user_consumed_bytes := varintlen

            when (io.fixed_writer_request.ready && io.consumer.output_valid) {
              ProtoaccLogger.logInfo("stringFieldState: sStringWait wait synced\n")
              when (descr_resp_reg.is_repeated) {
                urc_next_write_addr := urc_next_write_addr + (8.U)
                urc_elems_written := urc_elems_written + 1.U
              }

              obj_header_write_ptr := fixed_alloc_region_next_16B_aligned
              fixed_alloc_region_next := fixed_alloc_region_next_16B_aligned + 32.U

              handling_bytes := type_info === PROTO_TYPES.TYPE_BYTES

              stringLenNoNull := varintresult
              stringLenWithNull := varintresult + 1.U

              stringFieldState := sStringWriteHeader0

              ProtoaccLogger.logInfo("Handle String. is_repeated: %d, type: %d, ptr_waddr: 0x%x, ptr_value: 0x%x, stringlen: %d bytes\n",
                is_repeated, type_info, io.fixed_writer_request.bits.write_addr,
                fixed_alloc_region_next, varintresult)
            }
          }
        }

        is (sStringWriteHeader0) {
          ProtoaccLogger.logInfo("stringFieldState: sStringWriteHeader0\n")
          val header0_val = Mux(stringLenWithNull <= 16.U(64.W),
            obj_header_write_ptr + 16.U(64.W),
            fixed_alloc_region_next)
          data_write_ptr := header0_val

          set_fixed_write(obj_header_write_ptr, 3.U, header0_val)

          when(io.fixed_writer_request.ready) {
            stringFieldState := sStringWriteHeader1
            obj_header_write_ptr := obj_header_write_ptr + 8.U

            val stringLenWithNullPadded_calc = add_aligned(stringLenWithNull, 16)
            stringLenWithNullPadded := stringLenWithNullPadded_calc

            val fixed_alloc_region_increment = Mux(stringLenWithNull <= 16.U(64.W),
                0.U,
                stringLenWithNullPadded_calc
              )

            fixed_alloc_region_next := fixed_alloc_region_next + fixed_alloc_region_increment
            ProtoaccLogger.logInfo("Current alloc base: 0x%x, Next alloc base: 0x%x\n", fixed_alloc_region_next, fixed_alloc_region_next + fixed_alloc_region_increment)
          }
        }

        is (sStringWriteHeader1) {
          ProtoaccLogger.logInfo("stringFieldState: sStringWriteHeader1\n")

          set_fixed_write(obj_header_write_ptr, 3.U, stringLenNoNull)

          when(io.fixed_writer_request.ready) {
            stringFieldState := sStringMoveData
          }
        }

        is (sStringMoveData) {
          ProtoaccLogger.logInfo("stringFieldState: sStringMoveData\n")
          val inc_amt_log2 = 4.U
          io.fixed_writer_request.bits.write_width := inc_amt_log2
          io.fixed_writer_request.bits.write_addr := data_write_ptr


          when (io.fixed_writer_request.ready && !io.consumer.output_valid) {
            ProtoaccLogger.logInfo("fixed_writer ready but not consumer output\n")
          } .elsewhen (!io.fixed_writer_request.ready && io.consumer.output_valid) {
            ProtoaccLogger.logInfo("consumer output valid but not fixed_writer ready\n")
          }



          when (stringLenWithNullPadded > 16.U(64.W)) {
            val inc_amt = 16.U
            io.consumer.user_consumed_bytes := inc_amt

            val result128 = io.consumer.output_data(127, 0)
            io.fixed_writer_request.bits.write_data := result128

            io.fixed_writer_request.valid := io.consumer.output_valid
            io.consumer.output_ready := io.fixed_writer_request.ready
            when (io.fixed_writer_request.ready && io.consumer.output_valid) {
              ProtoaccLogger.logInfo("---stringLenWithNullPadded: %d\n", stringLenWithNullPadded)
              ProtoaccLogger.logInfo("---stringLenWithNull: %d\n", stringLenWithNull)
              ProtoaccLogger.logInfo("---stringLenNoNull: %d\n", stringLenNoNull)

              data_write_ptr := data_write_ptr + inc_amt
              stringLenWithNullPadded := stringLenWithNullPadded - inc_amt
              stringLenWithNull := stringLenWithNull - inc_amt
              stringLenNoNull := stringLenNoNull - inc_amt
            }
          } .elsewhen (stringLenWithNullPadded === 16.U(64.W) && stringLenNoNull =/= 0.U(64.W)) {
            val inc_amt = 16.U
            io.consumer.user_consumed_bytes := stringLenNoNull

            val result128 = io.consumer.output_data(127, 0)
            val stringLenNoNullShamt = stringLenNoNull(3, 0) << 3
            io.fixed_writer_request.bits.write_data := result128 & ((1.U(128.W) << (stringLenNoNullShamt)) - 1.U)

            io.fixed_writer_request.valid := io.consumer.output_valid
            io.consumer.output_ready := io.fixed_writer_request.ready
            when (io.fixed_writer_request.ready && io.consumer.output_valid) {
              ProtoaccLogger.logInfo("---stringLenWithNullPadded: %d\n", stringLenWithNullPadded)
              ProtoaccLogger.logInfo("---stringLenWithNull: %d\n", stringLenWithNull)
              ProtoaccLogger.logInfo("---stringLenNoNull: %d\n", stringLenNoNull)

              data_write_ptr := data_write_ptr + inc_amt
              stringLenWithNullPadded := stringLenWithNullPadded - inc_amt
              stringLenWithNull := stringLenWithNull - inc_amt
              stringLenNoNull := stringLenNoNull - inc_amt

              ProtoaccLogger.logInfo("---DONE STRING!\n")

              when (urc_valid && last_consumer_transaction) {
                fieldState := sEndBufCloseurcArray
              } .otherwise {
                fieldState := sReadKey
              }

              stringFieldState := sStringWait
            }
          } .elsewhen (stringLenWithNullPadded === 16.U(64.W) && stringLenNoNull === 0.U(64.W)) {
            when (io.fixed_writer_request.ready) {
              ProtoaccLogger.logInfo("---DONE STRING!\n")

              when (urc_valid && just_completed_buffer) {
                fieldState := sEndBufCloseurcArray
              } .otherwise {
                fieldState := sReadKey
              }

              stringFieldState := sStringWait
            }
            io.fixed_writer_request.bits.write_data := 0.U
            io.fixed_writer_request.valid := true.B
            io.consumer.output_ready := false.B
            io.consumer.user_consumed_bytes := 0.U
          } .otherwise {
            assert(false.B, "should be unreachable\n")
          }
        }
      }
    }

    is (sHandleStartGroup) {
      assert(fieldState =/= sHandleStartGroup, "Start Group not yet implemented")
    }

    is (sHandleEndGroup) {
      assert(fieldState =/= sHandleEndGroup, "End Group not yet implemented")
    }

    is (sEndBufCloseurcArray) {
      ProtoaccLogger.logInfo("fieldState: sEndBufCloseurcArray\n")

      when (urc_is_repeatedptrfield) {
        // OG: in 2 stages
        //   - 1st write to the current_size_ and capacity_proxy_
        //   - 2nd write to the allocated_size in Rep object
        // in 3 stages
        //   - S1 - write to current_size_ and capacity_proxy_ (if current_size_ == 1 or 0 then just to S4) otherwise do original S2
        //   - S2 - write to allocated_size in Rep object  -> go to S3 (ptr should already be tagged) (end from here)
        //   - S3 - in this case, need to move the ptr to the string and do not write alloc or tag it (end from here)
        // TODO: probably, based on size, don't write allocated_size and write the tag bit
        // this is writing to a rep_ pointer (when it should either be a rep pointer (if sz >= 2) or sz=1 std::string ptr or sz=0 null ptr)
        // if sz=1 or 0 then ptr's 0th bit is 0, otherwise it's 1
        // for now, assume that it is a rep object instead of doing the tagging (this will be overridden on the array close)
        when (urc_teardown_stage === 0.U) {
          set_fixed_write(urc_ptr_to_inobjsizes, 3.U, create_currentsize_capacityproxy(urc_elems_written))

          when (io.fixed_writer_request.ready) {
            ProtoaccLogger.logInfo("[unpacked repptrfield] closeout end of buf s0: elements_written:%d\n", urc_elems_written)
            when (urc_elems_written >= 2.U) {
              urc_teardown_stage := 1.U // skip to rep finish
            } .otherwise {
              urc_teardown_stage := 2.U // skip to non-rep finish
            }
          }
        } .elsewhen (urc_teardown_stage === 1.U) {
          set_fixed_write(urc_ptr_to_repallocsize, 3.U, urc_elems_written)

          when (io.fixed_writer_request.ready) {
            ProtoaccLogger.logInfo("[unpacked repptrfield] closeout end of buf s1\n")
            urc_valid := false.B
            array_alloc_region_next := add_aligned(urc_next_write_addr, 8)
            urc_teardown_stage := 0.U
            fieldState := sReadKey
          }
        } .otherwise { // urc_teardown_stage === 2.U
          set_fixed_write(descr_resp_extra_reg.ptr_to_repeatedptrfield_taggedreporelem, 3.U, initial_fixed_alloc_region) // write the string ptr (untagged) to the taggedrep ptr

          when (io.fixed_writer_request.ready) {
            ProtoaccLogger.logInfo("[unpacked repptrfield] closeout end of buf s2\n")
            urc_valid := false.B
            array_alloc_region_next := add_aligned(urc_next_write_addr, 8)
            urc_teardown_stage := 0.U
            fieldState := sReadKey
          }
        }
      } .otherwise {
        // this is a RepeatedField object, so just write to the current_size_ and total_size_
        set_fixed_write(urc_ptr_to_inobjsizes, 3.U, create_currentsize_totalsize(urc_elems_written))

        when (io.fixed_writer_request.ready) {
          ProtoaccLogger.logInfo("[unpacked repfield] closeout end of buf\n")

          urc_valid := false.B
          array_alloc_region_next := add_aligned(urc_next_write_addr, 8)
          fieldState := sReadKey
        }
      }
    }
  }
}

package protoacc.des

import chisel3._
import chisel3.util._
import freechips.rocketchip.tile._
import org.chipsalliance.cde.config._
import freechips.rocketchip.diplomacy._
import freechips.rocketchip.rocket.{TLBConfig}
import freechips.rocketchip.util.DecoupledHelper
import freechips.rocketchip.rocket.constants.MemoryOpConstants
import protoacc._

class BufInfoBundle extends Bundle {
  val len_bytes = UInt(32.W)
  val ADT_addr = UInt(64.W)
  val decoded_dest_base_addr = UInt(64.W)
  val min_field_no = UInt(32.W)
}

class LoadInfoBundle extends Bundle {
  val start_byte = UInt(4.W)
  val end_byte = UInt(4.W)
}


class MemLoaderConsumerBundle extends Bundle {
  val user_consumed_bytes = Input(UInt(log2Up(16+1).W))
  val available_output_bytes = Output(UInt(log2Up(16+1).W))
  val output_valid = Output(Bool())
  val output_ready = Input(Bool())
  val output_data = Output(UInt((16*8).W))
  val output_ADT_addr = Output(UInt(64.W))
  val output_min_field_no = Output(UInt(32.W))
  val output_decoded_dest_base_addr = Output(UInt(64.W))
  val output_last_chunk = Output(Bool())
}

class MemLoader()(implicit p: Parameters) extends Module
     with MemoryOpConstants {

  val io = IO(new Bundle {
    val l1helperUser = new L1MemHelperBundle

    val do_proto_parse_cmd = Flipped(Decoupled(new RoCCCommand))
    val proto_parse_info_cmd = Flipped(Decoupled(new RoCCCommand))

    val consumer = new MemLoaderConsumerBundle

  })

  assert(!io.do_proto_parse_cmd.valid || (io.do_proto_parse_cmd.valid &&
    io.proto_parse_info_cmd.valid),
    "got do proto parse command without valid proto parse info command!\n")

  val buf_info_queue = Module(new Queue(new BufInfoBundle, 16))

  val load_info_queue = Module(new Queue(new LoadInfoBundle, 256))

  val base_addr_bytes = io.do_proto_parse_cmd.bits.rs1
  val base_len = io.do_proto_parse_cmd.bits.rs2(31, 0)
  val min_field_no = io.do_proto_parse_cmd.bits.rs2 >> 32
  val base_addr_start_index = io.do_proto_parse_cmd.bits.rs1 & 0xF.U
  val aligned_loadlen =  base_len + base_addr_start_index
  val base_addr_end_index = (base_len + base_addr_start_index) & 0xF.U
  val base_addr_end_index_inclusive = (base_len + base_addr_start_index - 1.U) & 0xF.U
  val extra_word = ((aligned_loadlen & 0xF.U) =/= 0.U).asUInt

  val base_addr_bytes_aligned = (base_addr_bytes >> 4.U) << 4.U
  val words_to_load = (aligned_loadlen >> 4.U) + extra_word
  val words_to_load_minus_one = words_to_load - 1.U

  val ADT_addr = io.proto_parse_info_cmd.bits.rs1

  when (io.do_proto_parse_cmd.valid) {
    ProtoaccLogger.logInfo("base_addr_bytes: %x\n", base_addr_bytes)
    ProtoaccLogger.logInfo("base_len: %x\n", base_len)
    ProtoaccLogger.logInfo("base_addr_start_index: %x\n", base_addr_start_index)
    ProtoaccLogger.logInfo("aligned_loadlen: %x\n", aligned_loadlen)
    ProtoaccLogger.logInfo("base_addr_end_index: %x\n", base_addr_end_index)
    ProtoaccLogger.logInfo("base_addr_end_index_inclusive: %x\n", base_addr_end_index_inclusive)
    ProtoaccLogger.logInfo("extra_word: %x\n", extra_word)
    ProtoaccLogger.logInfo("base_addr_bytes_aligned: %x\n", base_addr_bytes_aligned)
    ProtoaccLogger.logInfo("words_to_load: %x\n", words_to_load)
    ProtoaccLogger.logInfo("words_to_load_minus_one: %x\n", words_to_load_minus_one)
    ProtoaccLogger.logInfo("ADT_addr: %x\n", ADT_addr)
    ProtoaccLogger.logInfo("min_field_no: %x\n", min_field_no)
    ProtoaccLogger.logInfo("decoded_dest_base_addr: %x\n", io.proto_parse_info_cmd.bits.rs2)
  }

  val request_fire = DecoupledHelper(
    io.l1helperUser.req.ready,
    io.do_proto_parse_cmd.valid,
    io.proto_parse_info_cmd.valid,
    buf_info_queue.io.enq.ready,
    load_info_queue.io.enq.ready
  )

  io.l1helperUser.req.bits.cmd := M_XRD
  io.l1helperUser.req.bits.size := log2Ceil(16).U
  io.l1helperUser.req.bits.data := 0.U

  val addrinc = RegInit(0.U(64.W))

  load_info_queue.io.enq.bits.start_byte := Mux(addrinc === 0.U, base_addr_start_index, 0.U)
  load_info_queue.io.enq.bits.end_byte := Mux(addrinc === words_to_load_minus_one, base_addr_end_index_inclusive, 15.U)


  when (request_fire.fire && (addrinc === words_to_load_minus_one)) {
    addrinc := 0.U
  } .elsewhen (request_fire.fire) {
    addrinc := addrinc + 1.U
  }

  when (io.do_proto_parse_cmd.fire) {
    ProtoaccLogger.logInfo("DO PROTO PARSE FIRE\n")
  }


  io.do_proto_parse_cmd.ready := request_fire.fire(io.do_proto_parse_cmd.valid,
                                            addrinc === words_to_load_minus_one)
  io.proto_parse_info_cmd.ready := request_fire.fire(io.proto_parse_info_cmd.valid,
                                            addrinc === words_to_load_minus_one)

  buf_info_queue.io.enq.valid := request_fire.fire(buf_info_queue.io.enq.ready,
                                            addrinc === 0.U)
  load_info_queue.io.enq.valid := request_fire.fire(load_info_queue.io.enq.ready)

  buf_info_queue.io.enq.bits.len_bytes := base_len
  buf_info_queue.io.enq.bits.ADT_addr := ADT_addr
  buf_info_queue.io.enq.bits.min_field_no := min_field_no
  buf_info_queue.io.enq.bits.decoded_dest_base_addr := io.proto_parse_info_cmd.bits.rs2

  io.l1helperUser.req.bits.addr := (base_addr_bytes_aligned) + (addrinc << 4)
  io.l1helperUser.req.valid := request_fire.fire(io.l1helperUser.req.ready)





  val NUM_QUEUES = 16
  val QUEUE_DEPTHS = 16 * 4
  val write_start_index = RegInit(0.U(log2Up(NUM_QUEUES+1).W))
  val mem_resp_queues = VecInit(Seq.fill(NUM_QUEUES)(Module(new Queue(UInt(8.W), QUEUE_DEPTHS)).io))
  for (queueno <- 0 until NUM_QUEUES) {
    mem_resp_queues(queueno).enq.bits := 0.U
    mem_resp_queues(queueno).deq.ready := false.B
  }

  val align_shamt = (load_info_queue.io.deq.bits.start_byte << 3)
  val memresp_bits_shifted = io.l1helperUser.resp.bits.data >> align_shamt

  for ( queueno <- 0 until NUM_QUEUES ) {
    mem_resp_queues((write_start_index +& queueno.U) % NUM_QUEUES.U).enq.bits := memresp_bits_shifted >> (queueno * 8)
  }

  val len_to_write = (load_info_queue.io.deq.bits.end_byte - load_info_queue.io.deq.bits.start_byte) +& 1.U

  val wrap_len_index_wide = write_start_index +& len_to_write
  val wrap_len_index_end = wrap_len_index_wide % NUM_QUEUES.U
  val wrapped = wrap_len_index_wide >= NUM_QUEUES.U

  when (load_info_queue.io.deq.valid) {
    ProtoaccLogger.logInfo("memloader start %x, end %x\n", load_info_queue.io.deq.bits.start_byte,
      load_info_queue.io.deq.bits.end_byte)
  }

  val resp_fire_noqueues = DecoupledHelper(
    io.l1helperUser.resp.valid,
    load_info_queue.io.deq.valid
  )
  val all_queues_ready = mem_resp_queues.map(_.enq.ready).reduce(_ && _)

  load_info_queue.io.deq.ready := resp_fire_noqueues.fire(load_info_queue.io.deq.valid, all_queues_ready)
  io.l1helperUser.resp.ready := resp_fire_noqueues.fire(io.l1helperUser.resp.valid, all_queues_ready)

  val resp_fire_allqueues = resp_fire_noqueues.fire() && all_queues_ready
  when (resp_fire_allqueues) {
    write_start_index := wrap_len_index_end
  }

  for ( queueno <- 0 until NUM_QUEUES ) {
    val use_this_queue = Mux(wrapped,
                             (queueno.U >= write_start_index) || (queueno.U < wrap_len_index_end),
                             (queueno.U >= write_start_index) && (queueno.U < wrap_len_index_end)
                            )
    mem_resp_queues(queueno).enq.valid := resp_fire_noqueues.fire() && use_this_queue && all_queues_ready
  }

  // for ( queueno <- 0 until NUM_QUEUES ) {
  //   when (mem_resp_queues(queueno).deq.valid) {
  //     ProtoaccLogger.logInfo("queueind %d, val %x\n", queueno.U, mem_resp_queues(queueno).deq.bits)
  //   }
  // }









  val read_start_index = RegInit(0.U(log2Up(NUM_QUEUES+1).W))


  val len_already_consumed = RegInit(0.U(32.W))

  val remapVecData = Wire(Vec(NUM_QUEUES, UInt(8.W)))
  val remapVecValids = Wire(Vec(NUM_QUEUES, Bool()))
  val remapVecReadys = Wire(Vec(NUM_QUEUES, Bool()))


  for (queueno <- 0 until NUM_QUEUES) {
    val remapindex = (queueno.U +& read_start_index) % NUM_QUEUES.U
    remapVecData(queueno) := mem_resp_queues(remapindex).deq.bits
    remapVecValids(queueno) := mem_resp_queues(remapindex).deq.valid
    mem_resp_queues(remapindex).deq.ready := remapVecReadys(queueno)
  }
  io.consumer.output_data := Cat(remapVecData.reverse)


  val buf_last = (len_already_consumed + io.consumer.user_consumed_bytes) === buf_info_queue.io.deq.bits.len_bytes
  val count_valids = remapVecValids.map(_.asUInt).reduce(_ +& _)
  val unconsumed_bytes_so_far = buf_info_queue.io.deq.bits.len_bytes - len_already_consumed

  val enough_data = Mux(unconsumed_bytes_so_far >= NUM_QUEUES.U,
                        count_valids === NUM_QUEUES.U,
                        count_valids >= unconsumed_bytes_so_far)

  io.consumer.available_output_bytes := Mux(unconsumed_bytes_so_far >= NUM_QUEUES.U,
                                    NUM_QUEUES.U,
                                    unconsumed_bytes_so_far)

  io.consumer.output_last_chunk := (unconsumed_bytes_so_far <= NUM_QUEUES.U)

  val read_fire = DecoupledHelper(
    io.consumer.output_ready,
    buf_info_queue.io.deq.valid,
    enough_data
  )

  when (read_fire.fire()) {
    ProtoaccLogger.logInfo("MEMLOADER READ: bytesread %d\n", io.consumer.user_consumed_bytes)

  }

  io.consumer.output_valid := read_fire.fire(io.consumer.output_ready)

  for (queueno <- 0 until NUM_QUEUES) {
    remapVecReadys(queueno) := (queueno.U < io.consumer.user_consumed_bytes) && read_fire.fire()
  }

  when (read_fire.fire()) {
    read_start_index := (read_start_index +& io.consumer.user_consumed_bytes) % NUM_QUEUES.U
  }

  buf_info_queue.io.deq.ready := read_fire.fire(buf_info_queue.io.deq.valid) && buf_last
  io.consumer.output_ADT_addr := buf_info_queue.io.deq.bits.ADT_addr
  io.consumer.output_min_field_no := buf_info_queue.io.deq.bits.min_field_no
  io.consumer.output_decoded_dest_base_addr := buf_info_queue.io.deq.bits.decoded_dest_base_addr

  when (read_fire.fire()) {
    when (buf_last) {
      len_already_consumed := 0.U
    } .otherwise {
      len_already_consumed := len_already_consumed + io.consumer.user_consumed_bytes
    }
  }

}

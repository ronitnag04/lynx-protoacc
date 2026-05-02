package protoacc

import scala.collection.immutable.{ListMap}

import chisel3._
import chisel3.util._

import org.chipsalliance.cde.config._

import freechips.rocketchip.tile._
import freechips.rocketchip.diplomacy._
import freechips.rocketchip.rocket.{TLBConfig, HellaCacheArbiter}
import freechips.rocketchip.util.DecoupledHelper
import freechips.rocketchip.rocket.constants.MemoryOpConstants
import freechips.rocketchip.tilelink._
import freechips.rocketchip.subsystem.{CacheBlockBytes}
import freechips.rocketchip.resources.{DiplomacyUtils}

import testchipip.soc.{BankedScratchpadParams}

import protoacc.des._

class ProtoAccel(opcodes: OpcodeSet, spadParams: Option[BankedScratchpadParams] = None)(implicit p: Parameters) extends LazyRoCC(
    opcodes = opcodes, nPTWPorts = 4) {
  override lazy val module = new ProtoAccelImp(this)
  //chisel3.experimental.annotate(midas.targetutils.EnableModelMultiThreadingAnnotation(module))

  val xbar = LazyModule(new TLXbar)

  // acc. memory accessors
  val mem_descr = LazyModule(new L1MemHelper("[m_descr]", numOutstandingReqs=p(ProtoAccelDesTopDescriptorReqs)))
  xbar.node := TLBuffer.chainNode(1) := mem_descr.masterNode
  val mem_memloader = LazyModule(new L1MemHelper("[m_memloader]", numOutstandingReqs=p(ProtoAccelDesTopMemloaderReqs), queueResponses=true))
  xbar.node := TLBuffer.chainNode(1) := mem_memloader.masterNode
  val mem_hasbits = LazyModule(new L1MemHelper(printInfo="[m_hasbits]", queueRequests=true))
  xbar.node := TLBuffer.chainNode(1) := mem_hasbits.masterNode
  val mem_fixedwriter = LazyModule(new L1MemHelperWriteFast(printInfo="[m_fixedwriter]", queueRequests=true))
  xbar.node := TLBuffer.chainNode(1) := mem_fixedwriter.masterNode

  val busBeatBytes = 16
  val intNode = spadParams match {
    case Some(BankedScratchpadParams(base, size, _, banks, subbanks, _, _, _, _, _)) => {
      val spad_xbar = LazyModule(new TLXbar).suggestName("protoaccdes_spad_xbar")

      // input multi-banked globally visible scratchpad
      val bankStripe = p(CacheBlockBytes)*subbanks
      val mask = (banks-1)*bankStripe
      val device = new MemoryDevice {
        override def describe(resources: ResourceBindings): Description = {
          Description(describeName("memory", resources), ListMap(
            "reg"         -> resources.map.filterKeys(DiplomacyUtils.regFilter).flatMap(_._2).map(_.value).toList,
            "device_type" -> Seq(ResourceString("memory")),
            "status"      -> Seq(ResourceString("okay"))
          ))
        }
      }
      (0 until banks).map { b =>
        val bank = LazyModule(new testchipip.soc.ScratchpadBankNonClockDiplomatic(
            subbanks,
            AddressSet(base + bankStripe * b, size - 1 - mask),
            busBeatBytes,
            device,
            BufferParams.default))
        bank.xbar := TLBuffer(BufferParams.default) := spad_xbar.node
      }

      // let outer memory come to scratchpad
      spad_xbar.node := TLBuffer() := TLWidthWidget(busBeatBytes) := stlNode
      // allow mem. reqs. to go to spad w/o going out to sbus
      spad_xbar.node := TLWidthWidget(busBeatBytes) := xbar.node

      TLFilter(TLFilter.mSubtract(Seq(AddressSet(base, size-1))))
    }
    case None => TLIdentityNode()
  }

  // let acc mem. reqs come be sent out (filter out all from spad)
  atlNode := intNode := TLWidthWidget(busBeatBytes) := xbar.node
}


class ProtoAccelImp(outer: ProtoAccel)(implicit p: Parameters) extends LazyRoCCModuleImp(outer)
with MemoryOpConstants {

  io.interrupt := false.B

  val cmd_router = Module(new CommandRouter)
  cmd_router.io.rocc_in <> io.cmd
  io.resp <> cmd_router.io.rocc_out

  io.mem.req.valid := false.B
  io.mem.s1_kill := false.B
  io.mem.s2_kill := false.B
  io.mem.keep_clock_enabled := true.B
  io.fpu_resp.ready := true.B
  io.fpu_req.valid := false.B
  io.fpu_req.bits := DontCare

  val memloader = Module(new MemLoader)
  memloader.io.do_proto_parse_cmd <> cmd_router.io.do_proto_parse_out
  memloader.io.proto_parse_info_cmd <> cmd_router.io.proto_parse_info_out

  outer.mem_memloader.module.io.userif <> memloader.io.l1helperUser
  outer.mem_memloader.module.io.sfence <> cmd_router.io.sfence_out
  outer.mem_memloader.module.io.status.valid := cmd_router.io.dmem_status_out.valid
  outer.mem_memloader.module.io.status.bits := cmd_router.io.dmem_status_out.bits.status
  io.ptw(0) <> outer.mem_memloader.module.io.ptw

  val field_handler = Module(new FieldHandler)

  outer.mem_descr.module.io.userif <> field_handler.io.l1helperUser
  outer.mem_descr.module.io.sfence <> cmd_router.io.sfence_out
  outer.mem_descr.module.io.status.valid := cmd_router.io.dmem_status_out.valid
  outer.mem_descr.module.io.status.bits := cmd_router.io.dmem_status_out.bits.status
  io.ptw(1) <> outer.mem_descr.module.io.ptw

  field_handler.io.consumer <> memloader.io.consumer
  field_handler.io.fixed_alloc_region_addr <> cmd_router.io.fixed_alloc_region_addr
  field_handler.io.array_alloc_region_addr <> cmd_router.io.array_alloc_region_addr

  cmd_router.io.completed_toplevel_bufs := field_handler.io.completed_toplevel_bufs

  val fixed_writer = Module(new FixedWriter)
  outer.mem_fixedwriter.module.io.userif <>  fixed_writer.io.l1helperUser
  outer.mem_fixedwriter.module.io.sfence <> cmd_router.io.sfence_out
  outer.mem_fixedwriter.module.io.status.valid := cmd_router.io.dmem_status_out.valid
  outer.mem_fixedwriter.module.io.status.bits := cmd_router.io.dmem_status_out.bits.status
  io.ptw(2) <> outer.mem_fixedwriter.module.io.ptw

  fixed_writer.io.fixed_writer_request <> field_handler.io.fixed_writer_request

  outer.mem_hasbits.module.io.userif <>  field_handler.io.l1helperUser2
  outer.mem_hasbits.module.io.sfence <> cmd_router.io.sfence_out
  outer.mem_hasbits.module.io.status.valid := cmd_router.io.dmem_status_out.valid
  outer.mem_hasbits.module.io.status.bits := cmd_router.io.dmem_status_out.bits.status
  io.ptw(3) <> outer.mem_hasbits.module.io.ptw

  cmd_router.io.no_writes_inflight := fixed_writer.io.no_writes_inflight && outer.mem_hasbits.module.io.userif.no_memops_inflight

  io.busy := false.B
}

package protoacc

import scala.collection.immutable.{ListMap}

import chisel3._
import chisel3.util._

import org.chipsalliance.cde.config._

import freechips.rocketchip.tile._
import freechips.rocketchip.diplomacy._
import freechips.rocketchip.rocket.{TLBConfig}
import freechips.rocketchip.util.DecoupledHelper
import freechips.rocketchip.rocket.constants.MemoryOpConstants
import freechips.rocketchip.tilelink._
import freechips.rocketchip.subsystem.{CacheBlockBytes}
import freechips.rocketchip.resources.{DiplomacyUtils}

import testchipip.soc.{BankedScratchpadParams}

import protoacc.ser._

// note: reduce ProtoAccelSerFieldHandlers (p) to reduce printfs to synthesize for firesim
class ProtoAccelSerializer(opcodes: OpcodeSet, spadParams: Option[BankedScratchpadParams] = None)(implicit p: Parameters) extends LazyRoCC(
    opcodes = opcodes, nPTWPorts = 3 + p(ProtoAccelSerFieldHandlers)) {
  val totalSerFieldHandlers: Int = p(ProtoAccelSerFieldHandlers)
  override lazy val module = new ProtoAccelSerializerImp(this, totalSerFieldHandlers)
  //chisel3.experimental.annotate(midas.targetutils.EnableModelMultiThreadingAnnotation(module))

  val xbar = LazyModule(new TLXbar).suggestName("protoaccser_spad_xbar")

  // protoacc assumes 128b mem. intf (uses TLWidthWidget to adapt to any bus width)
  val mem_descr1 = LazyModule(new L1MemHelper(printInfo="[m_serdescr1]", queueRequests=true, queueResponses=true))
  xbar.node := TLBuffer.chainNode(1) := mem_descr1.masterNode
  val mem_descr2 = LazyModule(new L1MemHelper(printInfo="[m_serdescr2]", queueRequests=true))
  xbar.node := TLBuffer.chainNode(1) := mem_descr2.masterNode

  val mem_serfieldhandlers = Seq.tabulate(totalSerFieldHandlers)(i => {
    val mem_serfieldhandler = LazyModule(new L1MemHelper(printInfo=s"[m_serfieldhandler${i}]", queueRequests=true, queueResponses=true))
    xbar.node := TLBuffer.chainNode(1) := mem_serfieldhandler.masterNode
    mem_serfieldhandler
  })

  val mem_serwriter = LazyModule(new L1MemHelperWriteFast(printInfo="[m_serwriter]", queueRequests=true))
  xbar.node := TLBuffer.chainNode(1) := mem_serwriter.masterNode

  val busBeatBytes = 16
  val intNode = spadParams match {
    case Some(BankedScratchpadParams(base, size, _, banks, subbanks, _, _, _, _, _)) => {
      val spad_xbar = LazyModule(new TLXbar)

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

class ProtoAccelSerializerImp(outer: ProtoAccelSerializer, totalSerFieldHandlers: Int)(implicit p: Parameters) extends LazyRoCCModuleImp(outer)
with MemoryOpConstants {

  io.interrupt := false.B

  val cmd_router = Module(new CommandRouterSerializer)
  cmd_router.io.rocc_in <> io.cmd
  io.resp <> cmd_router.io.rocc_out

  io.mem.req.valid := false.B
  io.mem.s1_kill := false.B
  io.mem.s2_kill := false.B
  io.mem.keep_clock_enabled := true.B
  io.fpu_resp.ready := true.B
  io.fpu_req.valid := false.B
  io.fpu_req.bits := DontCare

  val ser_descr_tab = Module(new SerDescriptorTableHandler)
  outer.mem_descr1.module.io.userif <> ser_descr_tab.io.l2helperUser1
  outer.mem_descr1.module.io.sfence <> cmd_router.io.sfence_out
  outer.mem_descr1.module.io.status.valid := cmd_router.io.dmem_status_out.valid
  outer.mem_descr1.module.io.status.bits := cmd_router.io.dmem_status_out.bits.status
  io.ptw(0) <> outer.mem_descr1.module.io.ptw

  outer.mem_descr2.module.io.userif <> ser_descr_tab.io.l2helperUser2
  outer.mem_descr2.module.io.sfence <> cmd_router.io.sfence_out
  outer.mem_descr2.module.io.status.valid := cmd_router.io.dmem_status_out.valid
  outer.mem_descr2.module.io.status.bits := cmd_router.io.dmem_status_out.bits.status
  io.ptw(1) <> outer.mem_descr2.module.io.ptw

  ser_descr_tab.io.serializer_cmd_in <> cmd_router.io.serializer_info_bundle_out

  val descr_to_fieldhandler_router = Module(new FieldDispatchRouter(totalSerFieldHandlers))
  descr_to_fieldhandler_router.io.fields_req_in <> ser_descr_tab.io.ser_field_handler_output
  val fieldhandler_to_memwriter_arbiter = Module(new MemWriteArbiter(totalSerFieldHandlers))

  // technically this has only been tested with ptw ports 2, 4, 5, ... (where 3 was given to the memwriter)
  val ser_field_handlers = Seq.tabulate(totalSerFieldHandlers)(i => {
    val ser_field_handler = Module(new SerFieldHandler(s"[serfieldhandler${i}]"))
    ser_field_handler.io.ops_in <> descr_to_fieldhandler_router.io.to_fieldhandlers(i)
    val mem_serfieldhandler = outer.mem_serfieldhandlers(i)
    mem_serfieldhandler.module.io.userif <> ser_field_handler.io.memread
    mem_serfieldhandler.module.io.sfence <> cmd_router.io.sfence_out
    mem_serfieldhandler.module.io.status.valid := cmd_router.io.dmem_status_out.valid
    mem_serfieldhandler.module.io.status.bits := cmd_router.io.dmem_status_out.bits.status
    io.ptw(3 + i) <> mem_serfieldhandler.module.io.ptw
    fieldhandler_to_memwriter_arbiter.io.from_fieldhandlers(i) <> ser_field_handler.io.writer_output
    ser_field_handler
  })

  val ser_memwriter = Module(new SerMemwriter)
  ser_memwriter.io.stringobj_output_addr <> cmd_router.io.stringalloc_region_addr_tail
  ser_memwriter.io.string_ptr_output_addr <> cmd_router.io.stringptr_region_addr
  ser_memwriter.io.memwrites_in <> fieldhandler_to_memwriter_arbiter.io.write_reqs_out
  ser_memwriter.io.l2io.resp <> outer.mem_serwriter.module.io.userif.resp
  ser_memwriter.io.l2io.no_memops_inflight := outer.mem_serwriter.module.io.userif.no_memops_inflight
  outer.mem_serwriter.module.io.userif.req <> ser_memwriter.io.l2io.req
  outer.mem_serwriter.module.io.sfence <> cmd_router.io.sfence_out
  outer.mem_serwriter.module.io.status.valid := cmd_router.io.dmem_status_out.valid
  outer.mem_serwriter.module.io.status.bits := cmd_router.io.dmem_status_out.bits.status
  io.ptw(2) <> outer.mem_serwriter.module.io.ptw

  cmd_router.io.no_writes_inflight := !(ser_memwriter.io.mem_work_outstanding)
  cmd_router.io.completed_toplevel_bufs := ser_memwriter.io.messages_completed
  io.busy := false.B
}

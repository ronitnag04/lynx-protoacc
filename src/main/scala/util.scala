package protoacc

import chisel3._
import chisel3.util._

import org.chipsalliance.cde.config._

import roccaccutils.logger._

import midas.targetutils.{SynthesizePrintf}

case object ProtoAccelPrintfEnable extends Field[Boolean](false)

// --- Deserializer (names mirror hyperscale-grpc-protoacc ProtoaccParams DESERIALIZER_*) ---

/** DESERIALIZER_TOP_DESCRIPTOR_REQS — outstanding TL reqs on top-level descriptor `L1MemHelper`. */
case object ProtoAccelDesTopDescriptorReqs extends Field[Int](4)

/** DESERIALIZER_TOP_MEMLOADER_REQS — outstanding TL reqs on top-level MemLoader `L1MemHelper`. */
case object ProtoAccelDesTopMemloaderReqs extends Field[Int](64)

/** DESERIALIZER_CR_ROCC_COMMANDS — RoCC command queue depth in deserializer `CommandRouter`. */
case object ProtoAccelDesCrRoccCommands extends Field[Int](2)

/** DESERIALIZER_DTH_L1_REQS — descriptor table handler L1 request queue depth. */
case object ProtoAccelDesDthL1Reqs extends Field[Int](4)

/** DESERIALIZER_DTH_FD_REQS — field-destination request queue depth in `DescriptorTableHandler`. */
case object ProtoAccelDesDthFdReqs extends Field[Int](4)

/** DESERIALIZER_DTH_FD_RESPS — field-destination / extra-meta response queue depths. */
case object ProtoAccelDesDthFdResps extends Field[Int](4)

/** DESERIALIZER_FW_L1_REQS — fixed-writer L1 request queue depth. */
case object ProtoAccelDesFwL1Reqs extends Field[Int](4)

/** DESERIALIZER_ML_BUF_INFO_Q — MemLoader `buf_info_queue` depth. */
case object ProtoAccelDesMlBufInfoQ extends Field[Int](16)

/** DESERIALIZER_ML_LOAD_INFO_Q — MemLoader `load_info_queue` depth. */
case object ProtoAccelDesMlLoadInfoQ extends Field[Int](256)

// --- Serializer (names mirror hyperscale-grpc-protoacc ProtoaccParams SERIALIZER_*) ---

/** SERIALIZER_TOP_NUM_FIELD_HANDLERS — parallel SerFieldHandler pipelines (+ PTW ports each). */
case object ProtoAccelSerFieldHandlers extends Field[Int](6)

/** SERIALIZER_CR_ROCC_COMMANDS — RoCC command queue depth in serializer `CommandRouterSerializer`. */
case object ProtoAccelSerCrRoccCommands extends Field[Int](2)

/** SERIALIZER_DTH_HASBITS_REQS — SerDescriptorTableHandler hasbits metadata queue depth. */
case object ProtoAccelSerDthHasbitsReqs extends Field[Int](4)

/** SERIALIZER_DTH_DESCRIPTOR_REQS — SerDescriptorTableHandler descriptor request queue depth. */
case object ProtoAccelSerDthDescriptorReqs extends Field[Int](4)

/** SERIALIZER_DTH_REG_RESPS — SerDescriptorTableHandler regular-response path queue depth. */
case object ProtoAccelSerDthRegResps extends Field[Int](10)

/** SERIALIZER_DTH_REQS_META — SerDescriptorTableHandler descriptor-req meta queue depth. */
case object ProtoAccelSerDthReqsMeta extends Field[Int](4)

/** SERIALIZER_DTH_FH_OUTPUTS — SerDescriptorTableHandler output to field handlers queue depth. */
case object ProtoAccelSerDthFhOutputs extends Field[Int](4)

/** SERIALIZER_MW_WRITE_INPUT — SerMemwriter writes_input_IF_Q depth. */
case object ProtoAccelSerMwWriteInput extends Field[Int](4)

/** SERIALIZER_MW_WRITE_INJECT — SerMemwriter write_inject_Q depth. */
case object ProtoAccelSerMwWriteInject extends Field[Int](4)

/** SERIALIZER_MW_WRITE_PTRS — SerMemwriter write_ptrs_Q depth. */
case object ProtoAccelSerMwWritePtrs extends Field[Int](10)

object ProtoaccLogger extends Logger {
  // optionally synthesize info msgs
  def logInfoImplPrintWrapper(printf: chisel3.printf.Printf)(implicit p: Parameters = Parameters.empty): chisel3.printf.Printf = {
    if (p(ProtoAccelPrintfEnable)) {
      SynthesizePrintf(printf)
    } else {
      printf
    }
  }

  // optionally synthesize critical msgs
  def logCriticalImplPrintWrapper(printf: chisel3.printf.Printf)(implicit p: Parameters = Parameters.empty): chisel3.printf.Printf = {
    if (p(ProtoAccelPrintfEnable)) {
      SynthesizePrintf(printf)
    } else {
      printf
    }
  }
}

object ProtoaccParams {
  val MAX_NESTED_LEVELS = 25
  val MAX_NESTED_LEVELS_WIDTH = log2Up(MAX_NESTED_LEVELS) + 1
}

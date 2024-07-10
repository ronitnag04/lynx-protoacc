package protoacc

import chisel3._
import chisel3.util._

import org.chipsalliance.cde.config._

import roccaccutils.logger._

import midas.targetutils.{SynthesizePrintf}

case object ProtoAccelPrintfEnable extends Field[Boolean](false)

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

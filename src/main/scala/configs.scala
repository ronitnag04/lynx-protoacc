package protoacc

import Chisel._

import org.chipsalliance.cde.config._

import freechips.rocketchip.tile._
import freechips.rocketchip.diplomacy._
import freechips.rocketchip.rocket.{TLBConfig}
import freechips.rocketchip.tilelink._

import protoacc.des._
import protoacc.ser._

class WithProtoAccel extends Config ((site, here, up) => {
  case ProtoTLB => Some(TLBConfig(nSets = 4, nWays = 4, nSectors = 1, nSuperpageEntries = 1))
  case BuildRoCC => up(BuildRoCC) ++ Seq(
    (p: Parameters) => {
      val protoacc = LazyModule.apply(new ProtoAccel(OpcodeSet.custom2)(p))
      protoacc
    },
    (p: Parameters) => {
      val protoaccser = LazyModule.apply(new ProtoAccelSerializer(OpcodeSet.custom3)(p))
      protoaccser
    }
  )
})

class WithProtoAccelSerOnly extends Config ((site, here, up) => {
  case ProtoTLB => Some(TLBConfig(nSets = 4, nWays = 4, nSectors = 1, nSuperpageEntries = 1))
  case BuildRoCC => up(BuildRoCC) ++ Seq(
    (p: Parameters) => {
      val protoaccser = LazyModule.apply(new ProtoAccelSerializer(OpcodeSet.custom3)(p))
      protoaccser
    }
  )
})

class WithProtoAccelDeserOnly extends Config ((site, here, up) => {
  case ProtoTLB => Some(TLBConfig(nSets = 4, nWays = 4, nSectors = 1, nSuperpageEntries = 1))
  case BuildRoCC => up(BuildRoCC) ++ Seq(
    (p: Parameters) => {
      val protoacc = LazyModule.apply(new ProtoAccel(OpcodeSet.custom2)(p))
      protoacc
    }
  )
})

class WithProtoAccelPrintf extends Config((site, here, up) => {
  case ProtoAccelPrintfEnable => true
})

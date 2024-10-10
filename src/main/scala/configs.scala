package protoacc

import org.chipsalliance.cde.config._

import freechips.rocketchip.tile._
import freechips.rocketchip.diplomacy._
import freechips.rocketchip.rocket.{TLBConfig}
import freechips.rocketchip.tilelink._
import testchipip.soc.{BankedScratchpadParams}

import protoacc.des._
import protoacc.ser._

class WithProtoAccelTLB extends Config((site, here, up) => {
  case ProtoTLB => Some(TLBConfig(nSets = 4, nWays = 4, nSectors = 1, nSuperpageEntries = 1))
})

class WithProtoAccelPrintf extends Config((site, here, up) => {
  case ProtoAccelPrintfEnable => true
})

class WithProtoAccelSerBase(spadParams: Option[BankedScratchpadParams] = None) extends Config((site, here, up) => {
  case BuildRoCC => up(BuildRoCC) ++ Seq(
    (p: Parameters) => {
      val protoaccser = LazyModule.apply(new ProtoAccelSerializer(OpcodeSet.custom3, spadParams)(p))
      protoaccser
    }
  )
})

class WithProtoAccelDeserBase(spadParams: Option[BankedScratchpadParams] = None) extends Config((site, here, up) => {
  case BuildRoCC => up(BuildRoCC) ++ Seq(
    (p: Parameters) => {
      val protoaccdes = LazyModule.apply(new ProtoAccel(OpcodeSet.custom2, spadParams)(p))
      protoaccdes
    }
  )
})

class WithProtoAccelSerOnly(spadParams: Option[BankedScratchpadParams] = None) extends Config(
  new WithProtoAccelTLB ++
  new WithProtoAccelSerBase(spadParams)
)

class WithProtoAccelDeserOnly(spadParams: Option[BankedScratchpadParams] = None) extends Config(
  new WithProtoAccelTLB ++
  new WithProtoAccelDeserBase(spadParams)
)

class WithProtoAccel extends Config(
  new WithProtoAccelTLB ++
  new WithProtoAccelSerBase ++
  new WithProtoAccelDeserBase
)

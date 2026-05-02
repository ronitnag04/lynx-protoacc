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

class WithProtoAccelSerFieldHandlers(n: Int) extends Config((site, here, up) => {
  case ProtoAccelSerFieldHandlers => n
}) {
  require(n >= 1, "ProtoAccelSerFieldHandlers must be >= 1")
}

class WithProtoAccelDesDescrOutstanding(n: Int) extends Config((site, here, up) => {
  case ProtoAccelDesTopDescriptorReqs => n
}) {
  require(n >= 1, "ProtoAccelDesTopDescriptorReqs must be >= 1")
}

class WithProtoAccelDesMemloaderOutstanding(n: Int) extends Config((site, here, up) => {
  case ProtoAccelDesTopMemloaderReqs => n
}) {
  require(n >= 1, "ProtoAccelDesTopMemloaderReqs must be >= 1")
}

class WithProtoAccelDesCrRoccCommands(n: Int) extends Config((site, here, up) => {
  case ProtoAccelDesCrRoccCommands => n
}) {
  require(n >= 1, "ProtoAccelDesCrRoccCommands must be >= 1")
}

class WithProtoAccelDesDthL1Reqs(n: Int) extends Config((site, here, up) => {
  case ProtoAccelDesDthL1Reqs => n
}) {
  require(n >= 1, "ProtoAccelDesDthL1Reqs must be >= 1")
}

class WithProtoAccelDesDthFdReqs(n: Int) extends Config((site, here, up) => {
  case ProtoAccelDesDthFdReqs => n
}) {
  require(n >= 1, "ProtoAccelDesDthFdReqs must be >= 1")
}

class WithProtoAccelDesDthFdResps(n: Int) extends Config((site, here, up) => {
  case ProtoAccelDesDthFdResps => n
}) {
  require(n >= 1, "ProtoAccelDesDthFdResps must be >= 1")
}

class WithProtoAccelDesFwL1Reqs(n: Int) extends Config((site, here, up) => {
  case ProtoAccelDesFwL1Reqs => n
}) {
  require(n >= 1, "ProtoAccelDesFwL1Reqs must be >= 1")
}

class WithProtoAccelDesMlBufInfoQ(n: Int) extends Config((site, here, up) => {
  case ProtoAccelDesMlBufInfoQ => n
}) {
  require(n >= 1, "ProtoAccelDesMlBufInfoQ must be >= 1")
}

class WithProtoAccelDesMlLoadInfoQ(n: Int) extends Config((site, here, up) => {
  case ProtoAccelDesMlLoadInfoQ => n
}) {
  require(n >= 1, "ProtoAccelDesMlLoadInfoQ must be >= 1")
}

class WithProtoAccelSerCrRoccCommands(n: Int) extends Config((site, here, up) => {
  case ProtoAccelSerCrRoccCommands => n
}) {
  require(n >= 1, "ProtoAccelSerCrRoccCommands must be >= 1")
}

class WithProtoAccelSerDthHasbitsReqs(n: Int) extends Config((site, here, up) => {
  case ProtoAccelSerDthHasbitsReqs => n
}) {
  require(n >= 1, "ProtoAccelSerDthHasbitsReqs must be >= 1")
}

class WithProtoAccelSerDthDescriptorReqs(n: Int) extends Config((site, here, up) => {
  case ProtoAccelSerDthDescriptorReqs => n
}) {
  require(n >= 1, "ProtoAccelSerDthDescriptorReqs must be >= 1")
}

class WithProtoAccelSerDthRegResps(n: Int) extends Config((site, here, up) => {
  case ProtoAccelSerDthRegResps => n
}) {
  require(n >= 1, "ProtoAccelSerDthRegResps must be >= 1")
}

class WithProtoAccelSerDthReqsMeta(n: Int) extends Config((site, here, up) => {
  case ProtoAccelSerDthReqsMeta => n
}) {
  require(n >= 1, "ProtoAccelSerDthReqsMeta must be >= 1")
}

class WithProtoAccelSerDthFhOutputs(n: Int) extends Config((site, here, up) => {
  case ProtoAccelSerDthFhOutputs => n
}) {
  require(n >= 1, "ProtoAccelSerDthFhOutputs must be >= 1")
}

class WithProtoAccelSerMwWriteInput(n: Int) extends Config((site, here, up) => {
  case ProtoAccelSerMwWriteInput => n
}) {
  require(n >= 1, "ProtoAccelSerMwWriteInput must be >= 1")
}

class WithProtoAccelSerMwWriteInject(n: Int) extends Config((site, here, up) => {
  case ProtoAccelSerMwWriteInject => n
}) {
  require(n >= 1, "ProtoAccelSerMwWriteInject must be >= 1")
}

class WithProtoAccelSerMwWritePtrs(n: Int) extends Config((site, here, up) => {
  case ProtoAccelSerMwWritePtrs => n
}) {
  require(n >= 1, "ProtoAccelSerMwWritePtrs must be >= 1")
}

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

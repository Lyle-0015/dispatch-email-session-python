from dataclasses import dataclass, field


@dataclass
class Shipment:
    reference: str
    status: str = "created"
    events: list[dict[str, str]] = field(default_factory=list)
    proof_files: list[str] = field(default_factory=list)
    open_exception: str | None = None

    def record(self, kind: str, proof_file: str | None = None, reason: str | None = None) -> None:
        if kind == "exception":
            if not reason or self.open_exception:
                raise ValueError("An exception needs a reason and cannot overlap another exception")
            self.open_exception = reason
            self.status = "exception"
        elif kind == "resolve":
            if not self.open_exception:
                raise ValueError("There is no open exception")
            self.open_exception = None
            self.status = "in_transit"
        elif kind == "delivered":
            if self.open_exception or not proof_file:
                raise ValueError("Delivery needs a proof file and no open exception")
            self.proof_files.append(proof_file)
            self.status = "delivered"
        elif kind == "in_transit":
            if self.open_exception or self.status == "delivered":
                raise ValueError("This shipment cannot move to in_transit")
            self.status = kind
        else:
            raise ValueError("Unknown shipment event")
        self.events.append({"kind": kind, "proof_file": proof_file or "", "reason": reason or ""})

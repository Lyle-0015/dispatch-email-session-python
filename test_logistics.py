import pytest

from logistics import Shipment


def test_exception_blocks_delivery_until_resolved():
    shipment = Shipment("PARCEL-42")
    shipment.record("exception", reason="Address needs confirmation")
    with pytest.raises(ValueError, match="no open exception"):
        shipment.record("delivered", proof_file="pod/PARCEL-42.pdf")
    assert shipment.events == [{"kind": "exception", "proof_file": "", "reason": "Address needs confirmation"}]
    shipment.record("resolve")
    shipment.record("delivered", proof_file="pod/PARCEL-42.pdf")
    assert shipment.status == "delivered"
    assert shipment.proof_files == ["pod/PARCEL-42.pdf"]

import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.join(REPO_ROOT, "python")

if PYTHON_DIR not in sys.path:
    sys.path.insert(0, PYTHON_DIR)

from tools.scan_market_surface import scan_market_surface, market_opportunity_to_c1_packet

def main():
    ops = scan_market_surface()
    print(f"Found {len(ops)} opportunities")

    for i, o in enumerate(ops):
        pkt = market_opportunity_to_c1_packet(o)

        if pkt and pkt.get("c1_candidate", False):
            print(f"\n--- C1 Candidate #{i} ---")
            print(pkt)

if __name__ == "__main__":
    main()

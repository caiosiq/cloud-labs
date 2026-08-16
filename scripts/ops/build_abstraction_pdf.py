#!/usr/bin/env python3
"""Generate Cloud Labs abstraction PDF for group-meeting handoff."""
from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

OUT = Path.home() / "Desktop" / "CloudLabs_Abstraction_Overview.pdf"
REPO_OUT = Path(
    r"c:\Users\User\OneDrive - The University of Chicago\cloud-labs\dist"
) / "CloudLabs_Abstraction_Overview.pdf"


class Doc(FPDF):
    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 9)
        self.set_text_color(100, 116, 139)
        self.cell(0, 8, "Cloud Labs - Abstraction Overview  |  Group meeting handoff", align="L")
        self.ln(10)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(148, 163, 184)
        self.cell(0, 8, f"Page {self.page_no()}/{{nb}}", align="C")

    def body(self, text: str) -> None:
        self.set_x(self.l_margin)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(51, 65, 85)
        self.multi_cell(0, 5.2, text)
        self.ln(1.5)

    def bullet(self, text: str) -> None:
        self.set_x(self.l_margin + 3)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(51, 65, 85)
        self.multi_cell(0, 5.0, f"-  {text}")

    def mono_line(self, text: str) -> None:
        self.set_x(self.l_margin + 3)
        self.set_font("Courier", "", 8.5)
        self.set_text_color(30, 41, 59)
        self.multi_cell(0, 4.4, text)

    def h1(self, text: str) -> None:
        self.set_x(self.l_margin)
        self.set_font("Helvetica", "B", 18)
        self.set_text_color(15, 23, 42)
        self.multi_cell(0, 9, text)
        self.ln(2)

    def h2(self, text: str) -> None:
        self.ln(3)
        self.set_x(self.l_margin)
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(14, 165, 233)
        self.multi_cell(0, 7, text)
        self.ln(1)

    def h3(self, text: str) -> None:
        self.ln(2)
        self.set_x(self.l_margin)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(30, 41, 59)
        self.multi_cell(0, 6, text)
        self.ln(0.5)

    def table(self, headers: list[str], rows: list[list[str]], col_w: list[float]) -> None:
        self.set_font("Helvetica", "B", 8.5)
        self.set_fill_color(226, 232, 240)
        self.set_text_color(30, 41, 59)
        for h, w in zip(headers, col_w):
            self.cell(w, 6.5, h, border=1, fill=True)
        self.ln()
        self.set_font("Helvetica", "", 8)
        self.set_text_color(51, 65, 85)
        fill = False
        for row in rows:
            if self.get_y() > self.h - self.b_margin - 10:
                self.add_page()
            if fill:
                self.set_fill_color(248, 250, 252)
            else:
                self.set_fill_color(255, 255, 255)
            for cell, w in zip(row, col_w):
                # Truncate overly long cells to keep layout stable
                text = cell if len(cell) < 95 else cell[:92] + "..."
                self.cell(w, 5.8, text, border=1, fill=True)
            self.ln()
            fill = not fill
        self.ln(2)


def build() -> Path:
    pdf = Doc(format="Letter")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()

    # Cover
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_text_color(15, 23, 42)
    pdf.set_y(40)
    pdf.set_x(pdf.l_margin)
    pdf.cell(0, 12, "Cloud Labs", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(14, 165, 233)
    pdf.cell(0, 10, "Abstraction Overview for Experimentalists", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(71, 85, 105)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        0,
        6,
        "A concise account of the shared laboratory language: components, "
        "tunables, measurables, primitives, kernels, and execution modes.\n\n"
        "Prepared for a group-meeting presentation of the abstraction "
        "(demo can follow separately).",
    )
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(100, 116, 139)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, 5, "Source: Cloud Labs Wiki + platform primitive registry (codebase).")

    # 1 Purpose
    pdf.add_page()
    pdf.h1("1. Purpose")
    pdf.body(
        "Autonomous optics work is often fragmented across vendor drivers, "
        "ad hoc notebooks, and lab-specific scripts that diverge from the UI. "
        "Cloud Labs exposes a shared laboratory language: the same actions "
        "available in the Twin UI are available from Python, under one mental "
        "model whether the bench is a teaching mock or a physical arm with cameras."
    )
    pdf.body(
        "The lab is modeled as an Optical Processing Unit (OPU): a remotely "
        "controlled machine that processes light paths. Authors express intent "
        "(move, measure, optimize) as validated actions. A coordinator validates "
        "and schedules that intent; an edge process that owns the instruments executes it."
    )
    pdf.h3("Design goals")
    for b in [
        "One vocabulary for UI and scripts (shared primitives).",
        "Components as scientific objects (parameters / tunables / measurables).",
        "Exclusive session lease so one author mutates a backend at a time.",
        "Closed-loop capture-score-actuate can run on the edge at camera rates.",
        "Mock-first APIs for teaching and CI, then the same contract on real hardware.",
        "Wiki Backends hub: live component/kernel reference with physical interpretation.",
    ]:
        pdf.bullet(b)

    # 2 Architecture
    pdf.h1("2. Three faces of the system")
    pdf.table(
        ["Face", "Role"],
        [
            ["Client (laptop / Twin / SDK)", "Express intent; hold a session lease"],
            ["Coordinator (central server)", "Validate primitives and jobs; queue; matchmake"],
            ["Edge (bench PC or mock)", "Own cameras, motors, robot; run tight loops"],
        ],
        [70, 110],
    )
    pdf.body(
        "Remote control does not mean the network performs physics. Intent "
        "travels over HTTP; physical actuation and sensing remain on the edge. "
        "When optimization must be frame-rate sensitive, the inner loop stays on the edge."
    )

    # 3 Lab model
    pdf.h1("3. Lab model: components")
    pdf.body(
        "Every part has a tag_id (e.g. tag_20). Twin renders it; scripts address it; "
        "the catalog states what it can do. Within each component, Cloud Labs separates "
        "three concepts often mixed in ad hoc lab software:"
    )
    pdf.h3("Parameters - identity")
    pdf.body(
        "Catalog identity: type (mirror, camera, ...), geometry hints, and which "
        "primitives are allowed. Parameters change when the part definition changes, "
        "not because a motor was jogged."
    )
    pdf.h3("Tunables - degrees of freedom you control")
    pdf.body(
        "A tunable is a lab DOF you can set (breadboard pose, motor angles, exposure, "
        "storage placement). There is one value for that DOF in the model. Scripts and "
        "Twin panels change it by issuing primitives (or confirming a Twin edit)-not by "
        "silently patching lab JSON."
    )
    pdf.body(
        "Lab observe recalculates the same tunable. A camera scan or motor tracker "
        "updates that DOF from the bench. That is not a measurable and not a second "
        "'reported tunable.'"
    )
    pdf.body(
        "In the Twin canvas, the ghost outline is a visual control for proposing a pose "
        "before you confirm; the solid shape shows the current lab-state pose after "
        "motion settles or a scan recalculates the tunable. Ghost is UI draft, not a "
        "separate model field."
    )
    pdf.h3("Measurables - observations without a matching tunable")
    pdf.body(
        "Measurables do not have a 1:1 tunable counterpart. They are obtained by "
        "measuring and are often stochastic or high-dimensional: camera frames "
        "(analysis layout: BGR uint8 HxWx3), kernel scores / feature vectors, and "
        "other sensor summaries. They refresh on record / capture / probe-not when "
        "you merely recalculate a motor angle."
    )
    pdf.h3("Working rule")
    pdf.bullet("Set tunables via actions (or Twin confirm).")
    pdf.bullet("The lab may recalculate those same tunables from observe.")
    pdf.bullet("Capture measurables; optimizers consume them (and may actuate tunables).")

    # 4 Primitives
    pdf.add_page()
    pdf.h1("4. Primitives - the full action inventory")
    pdf.body(
        "If components are nouns, primitives are verbs: validated lab actions shared by "
        "Twin, Command Console, recipes, and the cloudlabs SDK. That shared action set "
        "is the product contract. Exact availability on a given part still depends on "
        "that component's catalog capabilities.primitives; below is the full platform inventory."
    )

    pdf.h3("Read (queries)")
    pdf.table(
        ["Primitive", "Meaning"],
        [
            ["GET_TUNABLES", "Read tunable values for one tag"],
            ["GET_MEASURABLES", "Read current measurable values (may be stale / null)"],
        ],
        [55, 125],
    )

    pdf.h3("Observe / measure")
    pdf.table(
        ["Primitive", "Meaning"],
        [
            ["RECORD_MEASURABLES", "Capture fresh observations (e.g. camera frame)"],
            ["EVAL_KERNEL", "One-shot probe: capture + evaluate a measurement kernel"],
            ["SCAN", "Camera / localization scan pass (pose refresh path)"],
        ],
        [55, 125],
    )

    pdf.h3("Motion and placement")
    pdf.table(
        ["Primitive", "Meaning"],
        [
            ["MOVE_COMPONENT", "Place a part at a breadboard pose"],
            ["STORE_COMPONENT", "Move a part into inventory / storage"],
            ["PLACE_FROM_STORAGE", "Bring a stored part onto the breadboard"],
            ["AFFIRM_PLACED_AT_CURRENT", "Affirm hand-placed pose after leaving storage"],
            ["REPACK_STORAGE", "Repack a storage slot"],
            ["RECENTER_IN_STORAGE", "Recenter a part in its inventory slot"],
            ["REMOVE", "Remove a component from the active lab"],
        ],
        [55, 125],
    )

    pdf.h3("Motors and other setpoints")
    pdf.table(
        ["Primitive", "Meaning"],
        [
            ["MOVE_MOTOR", "Absolute / relative motor motion"],
            ["SET_MOTOR_SETPOINT", "Write motor setpoints (nominal_motor_positions)"],
            ["MOTOR_SEND_HOME", "Macro: home a motor (expands to motion)"],
            ["MOTOR_SET_ZERO", "Zero / reference a motor angle"],
            ["SET_EXPOSURE", "Camera exposure intent"],
            ["SET_LASER_OUTPUT", "Laser output-power intent"],
            ["APPLY_TUNABLES_PATCH", "Macro: patch several tunables in one request"],
        ],
        [55, 125],
    )

    pdf.h3("In-air manipulation")
    pdf.table(
        ["Primitive", "Meaning"],
        [
            ["PICK_COMPONENT", "Pick a part into the gripper"],
            ["HOVER", "Hold / move while held above the table"],
            ["PLACE_FROM_HOVER", "Place from hover onto the breadboard"],
            ["CONFIRM_HOLDING_TAG", "Confirm which tag is in the gripper"],
        ],
        [55, 125],
    )

    pdf.h3("TeleOp and live feed")
    pdf.table(
        ["Primitive", "Meaning"],
        [
            ["START_TELEOP", "Acquire per-component TeleOp lease"],
            ["END_TELEOP", "Release TeleOp lease"],
            ["TELEOP_JOG", "Push one jog frame under TeleOp"],
            ["TELEOP_GOTO", "Absolute goto at TeleOp speed"],
            ["START_LIVE_FEED", "Open a live video channel for a camera tag"],
            ["END_LIVE_FEED", "Close that live channel"],
        ],
        [55, 125],
    )

    pdf.h3("Closed-loop")
    pdf.table(
        ["Primitive", "Meaning"],
        [
            ["OPTIMIZE", "Closed-loop (or legacy) optimization session on the edge"],
        ],
        [55, 125],
    )

    pdf.h3("What is not a primitive")
    pdf.bullet("Registering a kernel - packaging an artifact (upload), not actuating hardware.")
    pdf.bullet("Listing backends - coordinator bookkeeping.")
    pdf.bullet("Reading whole lab state - a coordinator query; formal per-tag reads are GET_TUNABLES / GET_MEASURABLES.")

    # 5 Kernels
    pdf.add_page()
    pdf.h1("5. Kernels - measurements, not verbs")
    pdf.body(
        "A kernel is a measurement function the edge can run on a capture-typically a "
        "TorchScript model over a camera frame-returning a scalar score or a small "
        "feature vector (centroid, beam widths, ...). Primitives still perform actuation; "
        "kernels help OPTIMIZE (and authoring probes) observe."
    )
    pdf.table(
        ["Kind", "Definition"],
        [
            ["Catalog TorchScript", "Shipped .pt + manifest (demo.*, builtin.roi_centroid, ...)"],
            ["Session kernels", "Author nn.Module, register_kernel for this lease"],
            ["Runtime hooks", "Python ensemble plumbing (ensemble.eval.*); not image models"],
        ],
        [50, 130],
    )
    pdf.body(
        "probe_kernel maps to EVAL_KERNEL (one-shot). run_cobyla / run_optimize map to "
        "OPTIMIZE, with kernel ids as inputs to the objective. Registering a kernel does "
        "not move hardware."
    )

    # 6 Modes
    pdf.h1("6. Imperative vs closed-loop")
    pdf.h3("Imperative - author in the loop")
    pdf.body(
        "Each step is a primitive round-trip: decide, edge acts, observe, decide again. "
        "Author-side Python (plots, SciPy, branching) sits between steps. You can pull a "
        "camera measurable onto the laptop and score it locally-no kernel and no edge OPTIMIZE."
    )
    pdf.mono_line('tensor = lab.components.tag_22.measurable("camera_image").resolve(record=True)')
    pdf.mono_line("# score tensor.data (BGR HxWx3) locally, then set a tunable")
    pdf.body("Teaching script: scripts/language/06_client_side_measurable_loop.py")

    pdf.h3("Closed-loop - edge in the loop")
    pdf.body(
        "The author submits an OPTIMIZE job (variables, objective, kernels). The edge runs "
        "capture, score, actuate many times locally. The laptop waits for the job result. "
        "Appropriate for alignment loops and overnight / frame-rate-sensitive work."
    )
    pdf.body("Teaching script: scripts/language/03_closed_loop_catalog.py")

    # 7 Backends / lease
    pdf.h1("7. Backends and session lease")
    pdf.body(
        "One coordinator can host multiple labs (backends), e.g. mock.default for teaching "
        "and a named real bench. Twin, scripts, and Wiki Backends all target one backend at "
        "a time. Backend choice is an author decision, not a notebook environment variable."
    )
    pdf.body(
        "Mutating work acquires a session lease (exclusive right to mutate that backend). "
        "Client identity distinguishes Twin tabs and SDK processes (X-CloudLabs-Client)."
    )

    # 8 Surfaces
    pdf.h1("8. Product surfaces (working today)")
    pdf.table(
        ["Surface", "Role"],
        [
            ["Twin UI", "Interactive control and breadboard visualization"],
            ["cloudlabs SDK + language scripts", "Reproducible experiments and batch jobs"],
            ["Operations", "Job and edge health"],
            ["Wiki (Learn + Backends)", "Curriculum + live component/kernel/snapshot hub"],
        ],
        [55, 125],
    )
    pdf.h3("Working now (high level)")
    for b in [
        "Multi-backend coordinator with mock teaching bench and real-bench path.",
        "Component catalog with parameters, tunables, measurables, primitives, telemetry.",
        "Full primitive inventory above (dispatch + LabCommunicator adapters; mock coverage for teaching).",
        "Twin: placement, motors, live feeds, TeleOp, pose refresh, optimization UI hooks.",
        "SDK: connect/lease, prepare/catalog pins, fluent moves, measurable tensors, probe_kernel, OPTIMIZE/COBYLA, jobs.",
        "Kernels: catalog TorchScript + session registration; EVAL_KERNEL authoring probes.",
        "Wiki Learn curriculum + Backends gallery (components, kernels, snapshots).",
        "Client-side measurable loops and edge closed-loop as complementary modes.",
    ]:
        pdf.bullet(b)

    pdf.h3("Near-term / continuing work (honest scope)")
    for b in [
        "Deeper real-hardware hardening and availability UX for non-mock backends.",
        "Continued cleanup of legacy state mirrors so docs and runtime stay aligned "
        "(one tunable value; observe recalculates; measurables stay capture-only).",
        "Richer Wiki / SDK discovery for tensor axis semantics and physical interpretation.",
        "Broader snapshot / control-repo workflows for experiment provenance.",
        "Demo polish for group meetings (live Twin + script walkthrough next week).",
    ]:
        pdf.bullet(b)

    # 9 Cheat sheet
    pdf.add_page()
    pdf.h1("9. One-page cheat sheet")
    pdf.table(
        ["Concept", "One-line meaning"],
        [
            ["Component / tag_id", "Named part on the breadboard or in the library"],
            ["Parameter", "Identity / capability declaration (catalog)"],
            ["Tunable", "DOF you set; lab may recalculate the same field"],
            ["Measurable", "Captured observation with no 1:1 setpoint"],
            ["Primitive", "Validated verb (shared by UI and scripts)"],
            ["Kernel", "Measurement function (input to EVAL_KERNEL / OPTIMIZE)"],
            ["Backend", "One lab (mock or real) on the coordinator"],
            ["Lease", "Exclusive mutation rights for a session"],
            ["Imperative loop", "Author decides each step on the laptop"],
            ["Closed-loop OPTIMIZE", "Edge runs capture-score-actuate locally"],
        ],
        [45, 135],
    )

    pdf.ln(4)
    pdf.h2("Suggested talking order for Thursday")
    for i, line in enumerate(
        [
            "Why a shared language (UI = scripts).",
            "Three faces: Client / Coordinator / Edge.",
            "Component model: parameters, tunables, measurables (ghost = UI draft).",
            "Primitives as the verb inventory (show the tables).",
            "Kernels are measurements, not a second command language.",
            "Imperative vs edge OPTIMIZE (where the loop runs).",
            "Backends + lease; what is working today vs demo next week.",
        ],
        start=1,
    ):
        pdf.bullet(f"{i}. {line}")

    pdf.output(str(OUT))
    REPO_OUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        Path(REPO_OUT).write_bytes(OUT.read_bytes())
    except OSError:
        pass
    return OUT


if __name__ == "__main__":
    path = build()
    print(path)
    print("bytes", path.stat().st_size)

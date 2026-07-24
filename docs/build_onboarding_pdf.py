#!/usr/bin/env python
"""Render the Cloud Labs onboarding document to a typeset PDF.

Prose lives in ``BLOCKS`` below; figures live in ``docs/figures/``. Running
this script rebuilds ``docs/Onboarding.pdf``.

    python docs/build_onboarding_pdf.py
"""
from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    Image,
)
from reportlab.platypus.flowables import HRFlowable

NAVY = HexColor("#1f3b5c")
INK = HexColor("#1a1a1a")
MUTED = HexColor("#5a6b7b")
RULE = HexColor("#c3ccd6")
CODE_BG = HexColor("#f4f6f8")

FONTS = Path(r"C:\Windows\Fonts")
HERE = Path(__file__).resolve().parent
FIGURES = HERE / "figures"


def register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("Georgia", str(FONTS / "georgia.ttf")))
    pdfmetrics.registerFont(TTFont("Georgia-Bold", str(FONTS / "georgiab.ttf")))
    pdfmetrics.registerFont(TTFont("Georgia-Italic", str(FONTS / "georgiai.ttf")))
    pdfmetrics.registerFont(TTFont("Georgia-BoldItalic", str(FONTS / "georgiaz.ttf")))
    pdfmetrics.registerFontFamily(
        "Georgia",
        normal="Georgia",
        bold="Georgia-Bold",
        italic="Georgia-Italic",
        boldItalic="Georgia-BoldItalic",
    )
    pdfmetrics.registerFont(TTFont("Consolas", str(FONTS / "consola.ttf")))
    pdfmetrics.registerFont(TTFont("Consolas-Bold", str(FONTS / "consolab.ttf")))


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline(text: str, code_size: int = 9) -> str:
    out = []
    for tok in re.split(r"(`[^`]*`)", text):
        if len(tok) >= 2 and tok.startswith("`") and tok.endswith("`"):
            out.append(
                f'<font name="Consolas" size="{code_size}" color="#8c3b2f">'
                f"{_esc(tok[1:-1])}</font>"
            )
        else:
            seg = _esc(tok)
            seg = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", seg)
            seg = re.sub(r"\*(.+?)\*", r"<i>\1</i>", seg)
            out.append(seg)
    return "".join(out)


def styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle(
            "title", fontName="Georgia-Bold", fontSize=23, leading=27,
            textColor=NAVY, spaceAfter=3,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", fontName="Georgia-Italic", fontSize=12.5, leading=16,
            textColor=MUTED, spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body", fontName="Georgia", fontSize=10.5, leading=16,
            textColor=INK, alignment=TA_JUSTIFY, spaceAfter=9,
        ),
        "caption": ParagraphStyle(
            "caption", fontName="Georgia-Italic", fontSize=9, leading=12,
            textColor=MUTED, alignment=TA_CENTER, spaceBefore=4, spaceAfter=12,
        ),
        "h2": ParagraphStyle(
            "h2", fontName="Georgia-Bold", fontSize=14.5, leading=18,
            textColor=NAVY, spaceBefore=16, spaceAfter=3,
        ),
        "code": ParagraphStyle(
            "code", fontName="Consolas", fontSize=8.5, leading=12,
            textColor=INK, leftIndent=6, rightIndent=6,
            spaceBefore=2, spaceAfter=2,
        ),
    }


# Block kinds: title, subtitle, rule_title, h2, body, caption, figure, code
BLOCKS: list[tuple] = [
    ("title", "Cloud Labs"),
    ("subtitle", "An onboarding for scientists who run optics experiments"),
    ("rule_title", None),
    ("body",
     "This document is for someone who already knows how to work on an optics "
     "bench and has never heard of Cloud Labs. It explains what the system is "
     "for, walks through a short experiment, names the ideas that experiment "
     "uses, describes who is involved when a script runs, and then shows how to "
     "score images and close an optimization loop on the bench. Technical detail "
     "about wiring a new bench appears near the end; folder names appear only in "
     "a short appendix."),

    ("h2", "What Cloud Labs Is For"),
    ("body",
     "Cloud Labs lets you drive a physical optics bench \u2014 a robot arm, an "
     "industrial camera, a laser, steppers, and components on a breadboard \u2014 "
     "the same way you would drive a simulation: by writing a short script, or "
     "working in a browser, that says move this component, take a picture, score "
     "the picture, and optimize until the score is maximized. The central promise "
     "is that there is one vocabulary for everything you can do or observe, and "
     "that vocabulary is the same whether you are talking to a fast mock on a "
     "laptop, a physics simulation, or the real laser bench in the building."),
    ("figure", ("fig1_same_language_pdf.jpg", 6.5 * inch)),
    ("caption",
     "Figure 1. One experiment script addresses three kinds of bench. The "
     "language does not change; only which instrument answers."),
    ("body",
     "That promise matters because most of experimental work is iteration. You "
     "want to debug the logic of a scan on a laptop, rehearse the physics in "
     "simulation, and then run the same sequence on hardware without rewriting "
     "the science each time."),

    ("h2", "A First Experiment"),
    ("body",
     "The shortest useful path through Cloud Labs has four steps: connect to a "
     "named lab, move a component, record a camera image, and bring the pixels "
     "onto your machine as numbers you can plot or feed into analysis."),
    ("figure", ("fig3_experiment_sequence_pdf.jpg", 6.5 * inch)),
    ("caption",
     "Figure 2. A minimal experiment: open a session, command the table, capture "
     "an image, and materialize the pixels as a tensor."),
    ("body",
     "In Python that looks like the following. The named lab "
     "`\"mock.default\"` is a safe place to learn; `\"real.default\"` is the "
     "physical bench when it is registered and running."),
    ("code",
     "from cloudlabs import connect\n"
     "\n"
     'lab = connect("mock.default")\n'
     "\n"
     'lab.move_component("tag_22", "tunables.nominal_pose.x", 120.0)\n'
     "\n"
     "# record=True asks for a fresh capture, then brings the pixels here\n"
     'image = lab.measurable("tag_22", "camera_image").resolve_torch(record=True)\n'
     "# image is a torch.Tensor, HxWx3, uint8 BGR (values 0..255)\n"
     "# use .resolve(record=True) for a NumPy array instead"),
    ("body",
     "Nothing in that script mentions robots, camera drivers, or network paths. "
     "You choose a lab by name, speak in table coordinates and component tags, "
     "and ask for measurements when you need them. The rest of this document is "
     "an explanation of what those few lines are really doing."),

    ("h2", "The Vocabulary That Experiment Used"),
    ("body",
     "The script above already used the whole vocabulary, even if the words were "
     "not yet named."),
    ("body",
     "The verbs of the system are called primitives: the only operations that "
     "change or observe the world. Moving a component and recording measurables "
     "are primitives. There is deliberately no private side door such as "
     "`get_video_feed()` outside that vocabulary, because a lab-specific shortcut "
     "would break the promise that mock, simulation, and real speak the same "
     "language."),
    ("body",
     "Against those verbs stand a few kinds of nouns. Tunables are the commanded "
     "degrees of freedom \u2014 a component's nominal pose, a camera exposure, a "
     "laser power, a motor angle. In the script, `tunables.nominal_pose.x` is a "
     "tunable: you set it, and the bench tries to make it true. Measurables are "
     "observations with no commanded counterpart. A camera image is a measurable: "
     "you do not set the pixels; you capture them. That distinction is easy to "
     "miss and important for optics. A pose is still a tunable even when you read "
     "back a reported pose, because it has a setpoint; a camera frame is a "
     "measurable because it does not."),
    ("body",
     "Telemetry channels are the live feeds the catalog declares \u2014 a video "
     "stream, a teleop pose stream \u2014 and you may open them only after running "
     "the primitive that arms them. Kernels are small compiled scoring functions, "
     "usually TorchScript, that turn a camera frame into a number or a few "
     "features; they are the subject of a later section, because they are what "
     "make closed-loop optimization practical."),
    ("body",
     "The governing rule is simple: if you want something to happen, it must be "
     "nameable in this vocabulary. That is what keeps a script portable across "
     "benches."),

    ("h2", "Who Is Involved When That Script Runs"),
    ("body",
     "When you call `connect`, you are not talking to the robot directly. You are "
     "talking to a shared service that understands the lab language: it checks "
     "that your verbs are valid, holds a session so two people do not fight over "
     "the same arm, remembers which named lab you asked for, and forwards each "
     "command to the software that actually sits next to that bench. Your Python "
     "script and the browser interface called the Twin are two ways of speaking "
     "the same language to that service. The Twin is not a second, more powerful "
     "API; it is the same vocabulary with a visual shell."),
    ("figure", ("fig2_who_talks_pdf.jpg", 6.5 * inch)),
    ("caption",
     "Figure 3. You never address the robot from the script. The shared service "
     "understands the language; lab software next to the bench turns verbs into "
     "motion and captures."),
    ("body",
     "Beside the physical table runs lab software that belongs to the people who "
     "own that instrument. It receives the same verbs the mock and the simulation "
     "receive, and it alone may touch the arm, the camera, and the steppers. It "
     "owns the messy details \u2014 coordinate frames on that table, camera "
     "protocols, motion planning \u2014 so that none of those details leak into "
     "your script. From the scientist's point of view the important boundary is "
     "this: your notebook stays in the universal language; the lab keeps its "
     "hardware knowledge at the bench."),

    ("h2", "Preview for the Eye, Truth for the Measurement"),
    ("body",
     "Not every interaction with a bench has the same purpose, and Cloud Labs does "
     "not pretend one mechanism fits all of them."),
    ("figure", ("fig4_preview_vs_truth_pdf.jpg", 6.5 * inch)),
    ("caption",
     "Figure 4. Live video and hand teleop are for alignment and intuition. A "
     "latched capture ties image and motor angles to one timestamp and is what "
     "you use for analysis."),
    ("body",
     "Interactive control \u2014 hunting a laser spot by nudging a component \u2014 "
     "needs a continuous, low-latency conversation, so teleoperation lives on a "
     "live channel after you start a teleop session. Watching the table needs a "
     "video stream, not a thousand separate captures, so live feed is likewise a "
     "channel you arm and then watch. Deliberate science \u2014 one measurement, "
     "one score, one optimization step \u2014 uses ordinary commands that return "
     "when they finish, or a longer job when an optimizer must run for many seconds "
     "next to the camera."),
    ("body",
     "The subtler issue is not speed but correspondence. An industrial camera's "
     "shutter and readout can lag encoders by tens of milliseconds, and a remote "
     "browser always adds delay. Cloud Labs therefore does not claim that "
     "\u201cwhatever you see on the live stream right now\u201d is a scientific "
     "record. When you ask for a latched measurement, the capture carries a single "
     "time stamp for the fields taken together, with an honest note about how that "
     "time was obtained. The practical discipline for an experimentalist is "
     "straightforward: compose science from latched records, not from two "
     "independent live polls. The live streams are for the human eye; the latched "
     "observe is for the truth."),

    ("h2", "Scoring and Closed-Loop Optimization"),
    ("body",
     "Moving and photographing are only half of experimental work. The other half "
     "is deciding what \u201cgood\u201d looks like on the camera and searching for "
     "settings that make the bench better. In Cloud Labs that decision is expressed "
     "as a kernel: a small TorchScript function that takes an image and returns a "
     "scalar score or a short feature vector. Catalog kernels such as "
     "`demo.roi_mean_score` and `builtin.roi_centroid` ship with the system; for "
     "your own science you register a session kernel for the current lease \u2014 "
     "the same idea as those builtins, authored as a few lines of PyTorch and "
     "uploaded once so every later probe and optimization uses that exact artifact "
     "on the bench."),
    ("body",
     "The reason the score must run next to the camera becomes obvious as soon as "
     "the loop is fast. If each trial shipped a full frame to your laptop for "
     "SciPy to score, the network would dominate the experiment. Instead you "
     "submit an optimization job: the lab captures, scores with your kernel, and "
     "actuates locally, while your script only waits for the result."),
    ("figure", ("fig5_closed_loop_pdf.jpg", 6.5 * inch)),
    ("caption",
     "Figure 5. Shipping every frame to a laptop is the wrong geometry for a "
     "closed loop. Capture, kernel, and actuate stay on the bench; only the job "
     "request and the result cross the network."),
    ("body",
     "Here is a complete, minimal example that uses a session kernel in the same "
     "spirit as the catalog ROI-mean builtin: the mean intensity of the center "
     "half of the frame. You register it, probe once to learn the present score "
     "M0, then ask the lab to hold that score while a motor is allowed to move "
     "within bounds."),
    ("code",
     "import torch\n"
     "import torch.nn as nn\n"
     "from cloudlabs import connect\n"
     "\n"
     "class CenterRoiMean(nn.Module):\n"
     '    """Scalar score: mean intensity of the center half of the frame."""\n'
     "\n"
     "    def forward(self, image: torch.Tensor) -> torch.Tensor:\n"
     "        if image.dim() == 3:\n"
     "            image = image.unsqueeze(0)\n"
     "        _n, _c, h, w = image.shape\n"
     "        y0, y1 = h // 4, (3 * h) // 4\n"
     "        x0, x1 = w // 4, (3 * w) // 4\n"
     "        return image[:, :, y0:y1, x0:x1].mean()\n"
     "\n"
     'lab = connect("mock.default")\n'
     "\n"
     "# Upload the kernel for this session (not a lab verb — an artifact)\n"
     "kernel_id = lab.register_kernel(\n"
     '    "center_roi_mean",\n'
     "    module=CenterRoiMean(),\n"
     '    output_kind="scalar",\n'
     '    description="Center-ROI mean intensity (session copy of the ROI-mean idea)",\n'
     ")\n"
     "\n"
     "# One latched score on the bench — authoring measurement\n"
     'm0 = lab.probe_kernel("tag_22", "camera_image", kernel_id=kernel_id)\n'
     'print(f"M0 = {float(m0):.6f}")\n'
     "\n"
     "# Closed loop: edge runs capture → kernel → move; you wait for the job\n"
     "result = lab.run_cobyla(\n"
     "    variables=[\n"
     "        lab.variable(\n"
     '            "tag_20",\n'
     '            "tunables.nominal_motor_positions.1",\n'
     "            bounds=(-2.0, 2.0),\n"
     "            delta=True,\n"
     "        )\n"
     "    ],\n"
     "    match_kernel=lab.kernel_match(\n"
     '        "tag_22",\n'
     '        "camera_image",\n'
     "        kernel_id=kernel_id,\n"
     "        target=float(m0),\n"
     "    ),\n"
     "    max_evals=25,\n"
     ")\n"
     'print(result.get("status"))'),
    ("body",
     "A few consequences follow from this shape. `probe_kernel` is still one "
     "primitive round-trip: useful when you are authoring, plotting, or deciding "
     "a target by hand. `run_cobyla` (and the more general `run_optimize`) is a "
     "job: your laptop does not receive a Python callback on every evaluation, so "
     "the target \u2014 here M0 \u2014 must be baked into the job before you "
     "submit. Catalog kernels such as `builtin.roi_centroid` can be used the same "
     "way without registering anything; session kernels are how you bring your "
     "score onto the same path. On mock backends session registration is always "
     "allowed; on a physical bench it may need to be enabled by the people who "
     "operate that instrument."),

    ("h2", "Choosing a Lab by Name"),
    ("body",
     "The string you pass to `connect` \u2014 `\"mock.default\"`, "
     "`\"sim.default\"`, `\"real.default\"`, or another registered name \u2014 is "
     "the only handle a scientist normally needs. That name selects which "
     "instrument the shared service will talk to. Mock is for learning the "
     "interface without physics; simulation is for rehearsing dynamics; real is "
     "the physical bench when it is online."),
    ("body",
     "If you are only running experiments, you now have the full path: install "
     "the `cloudlabs` package, point it at the shared service, connect to the name "
     "you were given, and use move, resolve, probe, and optimize as above. The "
     "next section is for people who must stand up or maintain a bench; everyone "
     "else may skip to the status at the end."),

    ("h2", "If You Own or Wire a Bench"),
    ("body",
     "Standing up a new instrument always has the same shape. Lab software that "
     "speaks the shared vocabulary is started next to the hardware. The shared "
     "service is told, under a chosen lab name, where that software lives. From "
     "then on, `connect(\"your.lab.name\")` routes to it. Mock and simulation are "
     "registered the same way; they are not a different kind of system, only "
     "different instruments behind the same names."),
    ("body",
     "Two installable packages sit at opposite ends of that work. The `cloudlabs` "
     "package is what a scientist imports: it turns the vocabulary into calls "
     "against the shared service and returns ordinary Python objects, including "
     "NumPy arrays and PyTorch tensors when you resolve a measurable. The "
     "`cloudlabs-edge-dev` package is a development-time toolkit for whoever builds "
     "lab software: it can generate a starting template, check that the template is "
     "well formed, and run a conformance suite against a live lab URL. The running "
     "lab software does not import that toolkit; the toolkit helps you build and "
     "prove the lab, then steps aside."),
    ("body",
     "The lab software must honor the shared vocabulary and nothing else. It "
     "declares what the table looks like and what the process can do right now; it "
     "converts table coordinates into whatever frame the robot expects; and it "
     "refuses loudly when required calibration is missing rather than quietly "
     "assuming an identity transform that would mis-home the arm. How those files "
     "are laid out in a repository is secondary \u2014 the appendix below sketches "
     "it for readers who will open the code."),

    ("h2", "What Works Today"),
    ("body",
     "You can connect to a named lab, move components that the instrument supports, "
     "record camera images, and resolve those images through the shared service as "
     "NumPy arrays or PyTorch tensors from a laptop. Live video and teleoperation "
     "are available when the lab arms those channels. You can register a session "
     "kernel, probe it once, and run closed-loop optimization so that capture, "
     "score, and actuate stay next to the camera."),
    ("body",
     "Some inventory and storage verbs are not implemented on every bench yet; the "
     "system refuses them honestly rather than inventing success. On the real "
     "optics table, frame-calibration constants must still be supplied before the "
     "arm is driven for real; until they are, real coordinate transforms fail "
     "loudly instead of defaulting to a silent identity. That is intentional: a "
     "missing calibration should stop you, not misplace a component."),

    ("h2", "Appendix: Where Things Live, If You Open the Repository"),
    ("body",
     "The shared service lives in `backend/`. The browser Twin lives in "
     "`frontend/`. The scientist package is `packages/cloudlabs`. The builder "
     "toolkit is `packages/cloudlabs_edge_dev`. Shared wire definitions, including "
     "which name points at which lab, live under `schemas/`. Lab software for a "
     "given instrument lives in that lab's own repository under a "
     "`cloudlabs_edge/` folder; teaching copies for mock and simulation also live "
     "in this tree as `mock_edge/` and `simulation_edge/`. None of those paths is "
     "part of the experimental vocabulary \u2014 they are only a map for people "
     "who need to find the code."),
]


def make_code_block(text: str, st) -> Table:
    para = Preformatted(text, st["code"])
    tbl = Table([[para]], colWidths=[6.5 * inch])
    tbl.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
            ("BOX", (0, 0), (-1, -1), 0.6, RULE),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ])
    )
    return tbl


def make_figure(filename: str, width: float) -> Image:
    path = FIGURES / filename
    if not path.exists():
        raise FileNotFoundError(f"missing figure: {path}")
    # Preserve aspect ratio from the PNG.
    img = Image(str(path))
    aspect = float(img.imageHeight) / float(img.imageWidth)
    img.drawWidth = width
    img.drawHeight = width * aspect
    return img


def build_story(st) -> list:
    story: list = []
    for kind, payload in BLOCKS:
        if kind == "title":
            story.append(Paragraph(inline(payload), st["title"]))
        elif kind == "subtitle":
            story.append(Paragraph(inline(payload), st["subtitle"]))
        elif kind == "rule_title":
            story.append(HRFlowable(
                width="100%", thickness=1.4, color=NAVY,
                spaceBefore=6, spaceAfter=12,
            ))
        elif kind == "h2":
            story.append(KeepTogether([
                Paragraph(inline(payload), st["h2"]),
                HRFlowable(
                    width="100%", thickness=0.75, color=RULE,
                    spaceBefore=2, spaceAfter=8,
                ),
            ]))
        elif kind == "body":
            story.append(Paragraph(inline(payload), st["body"]))
        elif kind == "caption":
            story.append(Paragraph(inline(payload), st["caption"]))
        elif kind == "figure":
            filename, width = payload
            story.append(Spacer(1, 4))
            story.append(make_figure(filename, width))
        elif kind == "code":
            story.append(Spacer(1, 4))
            story.append(make_code_block(payload, st))
            story.append(Spacer(1, 8))
    return story


def footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    x0 = doc.leftMargin
    x1 = doc.pagesize[0] - doc.rightMargin
    y = 0.62 * inch
    canvas.line(x0, y, x1, y)
    canvas.setFont("Georgia-Italic", 8.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(x0, y - 12, "Cloud Labs \u2014 Onboarding")
    canvas.drawRightString(x1, y - 12, f"{canvas.getPageNumber()}")
    canvas.restoreState()


def main() -> None:
    register_fonts()
    st = styles()
    out = HERE / "Onboarding.pdf"
    doc = SimpleDocTemplate(
        str(out),
        pagesize=LETTER,
        leftMargin=inch,
        rightMargin=inch,
        topMargin=0.85 * inch,
        bottomMargin=0.9 * inch,
        title="Cloud Labs",
        author="Cloud Labs",
    )
    doc.build(build_story(st), onFirstPage=footer, onLaterPages=footer)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

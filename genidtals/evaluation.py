"""Accuracy evaluation: genuine/impostor score collection and error rates.

Dataset layout for :func:`load_dataset`: one sub-directory per subject,
each holding that subject's images::

    dataset/
      subject_001/ img1.png img2.png ...
      subject_002/ ...

The first ``enroll_count`` images of each subject become its references;
the remaining images are probes compared against every subject.
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .calibration import DEFAULT_CALIBRATION, Calibration
from .config import SECURITY_LEVELS
from .errors import GenIDtalsError
from .matching import Probe, match
from .preprocessing import ImageInput, load_image

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


@dataclass
class ScoreSet:
    genuine: list = field(default_factory=list)   # per-modality score dicts
    impostor: list = field(default_factory=list)
    failed_to_enroll: int = 0
    failed_to_acquire: int = 0


@dataclass
class Report:
    genuine: np.ndarray
    impostor: np.ndarray
    eer: float
    eer_threshold: float
    failed_to_enroll: int = 0
    failed_to_acquire: int = 0

    def far(self, threshold: float) -> float:
        return float((self.impostor >= threshold).mean())

    def frr(self, threshold: float) -> float:
        return float((self.genuine < threshold).mean())

    def operating_points(self, calibration: Calibration) -> dict:
        out = {}
        for level in SECURITY_LEVELS:
            t = calibration.threshold(level)
            out[level] = {"threshold": t, "far": self.far(t), "frr": self.frr(t)}
        return out

    def to_dict(self, calibration: Calibration | None = None) -> dict:
        d = {
            "genuine_comparisons": int(len(self.genuine)),
            "impostor_comparisons": int(len(self.impostor)),
            "eer": round(self.eer, 6),
            "eer_threshold": round(self.eer_threshold, 2),
            "genuine_min": round(float(self.genuine.min()), 2),
            "genuine_mean": round(float(self.genuine.mean()), 2),
            "impostor_max": round(float(self.impostor.max()), 2),
            "impostor_mean": round(float(self.impostor.mean()), 2),
            "failed_to_enroll": self.failed_to_enroll,
            "failed_to_acquire": self.failed_to_acquire,
        }
        if calibration is not None:
            d["operating_points"] = self.operating_points(calibration)
        return d


def equal_error_rate(genuine: np.ndarray, impostor: np.ndarray) -> tuple[float, float]:
    thresholds = np.unique(np.concatenate([genuine, impostor, [0.0, 100.0]]))
    far = np.array([(impostor >= t).mean() for t in thresholds])
    frr = np.array([(genuine < t).mean() for t in thresholds])
    i = int(np.argmin(np.abs(far - frr)))
    return float((far[i] + frr[i]) / 2), float(thresholds[i])


def collect_scores(subjects: dict, enroll_count: int = 3,
                   calibration: Calibration = DEFAULT_CALIBRATION) -> ScoreSet:
    """Compare every probe against every subject's references."""
    out = ScoreSet()
    refs, probes = {}, {}
    for sid, images in subjects.items():
        templates = []
        for img in images[:enroll_count]:
            try:
                templates.append(Probe.from_gray(load_image(img)).features)
            except GenIDtalsError:
                out.failed_to_enroll += 1
        if templates:
            refs[sid] = templates
        probes[sid] = []
        for img in images[enroll_count:]:
            try:
                probes[sid].append(Probe.from_gray(load_image(img)))
            except GenIDtalsError:
                out.failed_to_acquire += 1
    for sid, plist in probes.items():
        for probe in plist:
            for rid, templates in refs.items():
                parts = match(probe, templates, calibration).modalities
                (out.genuine if rid == sid else out.impostor).append(parts)
    return out


def report(scores: ScoreSet, calibration: Calibration = DEFAULT_CALIBRATION) -> Report:
    if not scores.genuine or not scores.impostor:
        raise GenIDtalsError("Evaluation needs both genuine and impostor comparisons")
    gen = np.array([calibration.fuse(s) for s in scores.genuine])
    imp = np.array([calibration.fuse(s) for s in scores.impostor])
    eer, t = equal_error_rate(gen, imp)
    return Report(gen, imp, eer, t, scores.failed_to_enroll, scores.failed_to_acquire)


def load_dataset(root) -> dict[str, list[Path]]:
    root = Path(root)
    subjects = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        files = sorted(f for f in d.iterdir() if f.suffix.lower() in IMAGE_SUFFIXES)
        if files:
            subjects[d.name] = files
    if len(subjects) < 2:
        raise GenIDtalsError(f"{root} must contain at least two subject directories with images")
    return subjects


def synthetic_subjects(count: int = 30, images_per_subject: int = 6, hard: bool = True,
                       seed: int = 0) -> dict[str, list[ImageInput]]:
    from . import synthetic

    rng = np.random.default_rng(seed)
    return {
        f"synthetic_{seed}_{i}": [synthetic.capture(sp, rng, hard=hard) for _ in range(images_per_subject)]
        for i, sp in ((i, synthetic.make_specimen(seed * 100_000 + i)) for i in range(count))
    }

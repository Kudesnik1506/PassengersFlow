"""Классические методы: вертикальные края, движение, колёса как масштаб.

Три метода без обученных моделей. Общее у них одно: каждый сводит кузов к
профилю по горизонтали — «насколько эта колонка пикселей похожа на дверь» — и
дальше режет профиль на отрезки. Различаются они только тем, что считают
признаком двери. Общая часть вынесена, чтобы разница между методами была
видна глазом, а не тонула в одинаковой обвязке.

Ни один из них не знает, сколько дверей у машины. Это знание приходит извне
(таблица 3) и работает в ансамбле — здесь методы отвечают честно, столько
кандидатов, сколько нашли.
"""

from __future__ import annotations

import numpy as np

from ..cases import Case
from ..finders import Box, FrameSource, crop_body, to_pixels

# Ширина дверного проёма в долях длины кузова. Не константа регламента, а
# рабочая гипотеза стенда: у трёхдверного 12-метрового автобуса проём около
# 1,2 м при длине 12 м — это и есть верхняя граница; нижняя взята с запасом на
# одностворчатую дверь. Проверяется она ровно этим тестом: если эталонные
# проёмы в полосу не попадут, гипотеза неверна и это будет видно.
MIN_DOOR_FRAC = 0.035
MAX_DOOR_FRAC = 0.16
# Полоса по высоте кузова, в которой ищется дверь: ниже окон, выше колёс.
BAND_Y0, BAND_Y1 = 0.35, 0.95
RESIZE_W = 512


def _band(image: np.ndarray, body: Box) -> np.ndarray | None:
    """Полоса борта из кадра, приведённая к постоянной ширине."""
    import cv2

    crop, actual = crop_body(image, body)
    if crop.size == 0 or crop.shape[1] < 32:
        return None
    h = crop.shape[0]
    y0, y1 = int(BAND_Y0 * h), int(BAND_Y1 * h)
    strip = crop[max(0, y0):max(y0 + 1, y1), :]
    scale = RESIZE_W / strip.shape[1]
    return cv2.resize(strip, (RESIZE_W, max(8, int(strip.shape[0] * scale))))


def _runs(profile: np.ndarray, percentile: float) -> list[tuple[int, int, float]]:
    """Непрерывные участки профиля СТРОГО выше порога-процентиля.

    Строго — не придирка. У ровного борта профиль почти везде нулевой, и
    процентиль тоже оказывается нулём; нестрогое сравнение объявило бы выше
    порога весь кадр, и шесть границ створок слиплись бы в один участок во всю
    ширину. Замерено на синтетическом кадре с тремя явными проёмами: метод
    краёв находил ноль дверей из трёх.
    """
    if profile.size == 0:
        return []
    threshold = float(np.percentile(profile, percentile))
    above = profile > threshold
    runs: list[tuple[int, int, float]] = []
    start = None
    for i, flag in enumerate(above):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((start, i, float(profile[start:i].mean())))
            start = None
    if start is not None:
        runs.append((start, len(above), float(profile[start:].mean())))
    return runs


def _segments_to_boxes(runs, case: Case) -> list[Box]:
    """Отрезки профиля → проёмы в пикселях кадра, с отсевом по ширине."""
    boxes: list[Box] = []
    for start, end, score in runs:
        x0f, x1f = start / RESIZE_W, end / RESIZE_W
        if not (MIN_DOOR_FRAC <= x1f - x0f <= MAX_DOOR_FRAC):
            continue
        boxes.append((to_pixels((x0f, BAND_Y0, x1f, BAND_Y1), case.body_px), score))
    boxes.sort(key=lambda t: -t[1])
    return [b for b, _ in boxes]


def _needs_cv2() -> str | None:
    try:
        import cv2  # noqa: F401
    except ImportError as exc:
        return f"opencv не установлен: {exc}"
    return None


class EdgeFinder:
    """Дверь как промежуток между двумя сильными вертикальными краями.

    Отличается от нашего пиксельного метода признаком: тот ищет участки,
    непохожие на цвет борта, этот — сами границы створок. На машине, чей борт
    того же цвета, что и дверь, второй имеет шанс там, где первый слеп.
    """

    tag = "edges"
    title = "вертикальные края створок"

    EDGE_PERCENTILE = 88

    def available(self) -> str | None:
        return _needs_cv2()

    def find(self, case: Case, frames: FrameSource) -> list[Box]:
        import cv2

        band = _band(frames.frame(case.frame_idx), case.body_px)
        if band is None:
            return []
        gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
        sobel = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        profile = sobel.mean(axis=0)
        profile = profile / (profile.max() or 1.0)

        peaks = [ (a + b) // 2 for a, b, _ in _runs(profile, self.EDGE_PERCENTILE) ]
        runs = []
        for left, right in zip(peaks, peaks[1:]):
            if right - left < 4:
                continue
            strength = float(min(profile[left], profile[right]))
            runs.append((left, right, strength))
        return _segments_to_boxes(runs, case)


class MotionFinder:
    """Дверь как единственное, что меняется у стоящей машины.

    Кузов неподвижен, створки ездят, люди входят — разность соседних кадров
    должна светиться ровно в проёмах. Слабое место известно заранее: автобус
    качается на подвеске, и тогда светится весь борт. Именно это и проверяется.
    """

    tag = "motion"
    title = "временная разность"

    OFFSETS = (-30, -15, 15, 30)  # ±0,5 и ±1 с при 30 к/с
    DIFF_PERCENTILE = 85

    def available(self) -> str | None:
        return _needs_cv2()

    def find(self, case: Case, frames: FrameSource) -> list[Box]:
        import cv2

        base = _band(frames.frame(case.frame_idx), case.body_px)
        if base is None:
            return []
        base_gray = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY).astype(np.float32)

        stack = []
        for offset in self.OFFSETS:
            idx = case.frame_idx + offset
            if idx < 0:
                continue
            try:
                other = _band(frames.frame(idx), case.body_px)
            except RuntimeError:
                continue
            if other is None or other.shape != base.shape:
                continue
            other_gray = cv2.cvtColor(other, cv2.COLOR_BGR2GRAY).astype(np.float32)
            stack.append(np.abs(other_gray - base_gray))
        if not stack:
            return []

        profile = np.median(np.stack(stack), axis=0).mean(axis=0)
        profile = profile / (profile.max() or 1.0)
        return _segments_to_boxes(_runs(profile, self.DIFF_PERCENTILE), case)


class WheelFinder:
    """Колёса как масштаб: они задают метр, метр отсеивает неправдоподобное.

    Сами колёса дверь не показывают. Их польза в другом: диаметр колеса
    городского автобуса известен с точностью до сантиметров, и по нему
    пиксели переводятся в метры. Кандидат шириной в полметра или в три —
    не дверь, чем бы он ни выглядел.

    Колёса ищутся преобразованием Хафа по нижней трети кузова. Метод заведомо
    слабый на ракурсе под углом (круг превращается в эллипс) — это и есть
    предмет проверки, а не недосмотр.
    """

    tag = "wheels"
    title = "колёса как масштаб + правдоподобие ширины"

    WHEEL_BAND = (0.62, 1.0)  # доля высоты кузова, где искать колёса
    WHEEL_DIAMETER_M = 1.0  # городской автобус: 275/70 R22.5 ≈ 1,04 м
    DOOR_MIN_M, DOOR_MAX_M = 0.6, 1.5

    def available(self) -> str | None:
        return _needs_cv2()

    def find(self, case: Case, frames: FrameSource) -> list[Box]:
        import cv2

        image = frames.frame(case.frame_idx)
        crop, actual = crop_body(image, case.body_px)
        if crop.size == 0:
            return []
        h, w = crop.shape[:2]
        y0 = int(self.WHEEL_BAND[0] * h)
        band = crop[y0:, :]
        gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
        gray = cv2.medianBlur(gray, 5)
        circles = cv2.HoughCircles(
            gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=w // 8,
            param1=120, param2=40,
            minRadius=max(4, h // 12), maxRadius=max(8, h // 3),
        )
        if circles is None:
            return []
        radii = [c[2] for c in circles[0]]
        px_per_m = (2 * float(np.median(radii))) / self.WHEEL_DIAMETER_M
        if px_per_m <= 0:
            return []

        # Масштаб есть — просим края назвать кандидатов и оставляем те, чья
        # ширина укладывается в реальные метры.
        raw = EdgeFinder().find(case, frames)
        return [b for b in raw
                if self.DOOR_MIN_M <= (b[2] - b[0]) / px_per_m <= self.DOOR_MAX_M]

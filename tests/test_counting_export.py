"""Выгрузка пакетов на диск: куда можно писать кадры и что о них известно.

Счётная модель у нас работает по подписке, а не по ключу API: кадры уходят ей
не в теле запроса, а файлами на диске, которые читает слепой счётчик. От этого
появляется место, которого при вызове по ключу не было, — каталог с кадрами.
Запрет 7 его и стережёт: кадры не публикуются, но в счётный API уходить могут.
Значит, писать их можно ровно в одно место — под `out/`, который закрыт
`.gitignore`. Проверяется это здесь, а не глазами: промах необратим.

Остальные три проверки держат форму пакета, на которую опирается промпт: имя
кадра несёт его секунду, порядок кадров — временной, а дверь без кадров не
создаёт пустого каталога («не считали» ≠ «ноль», см. `packages.py`).
"""

from __future__ import annotations

import json

import pytest
from paxcount.counting.export import FramesOutsideOutDirError, write_packages
from paxcount.counting.packages import Package, PackageFrame

JPEG = b"\xff\xd8\xff\xe0not-a-real-jpeg"


def package(door: int = 1, times: tuple[float, ...] = (0.0, 0.5, 1.0)) -> Package:
    return Package(
        visit_id=1, door=door, visit_key="2|2026-09-10T06:56:54", camera="2",
        width=320, height=240,
        frames=tuple(PackageFrame(t=t, jpeg=JPEG) for t in times),
    )


def test_frames_may_not_be_written_outside_out_dir(tmp_path):
    """Единственное разрешённое место для кадров — `out/`."""
    with pytest.raises(FramesOutsideOutDirError):
        write_packages([package()], tmp_path / "куда-нибудь", "эталон-двери",
                        prompt="считайте", out_dir=tmp_path / "out")


def test_frame_file_name_carries_its_second(tmp_path):
    """Промпт обещает секунду в имени кадра — она обязана там быть."""
    written = write_packages([package(times=(0.0, 1.5))], tmp_path / "out" / "llm",
                              "эталон-двери", prompt="считайте",
                              out_dir=tmp_path / "out")
    names = [p.name for p in written[0].frames]
    assert names == ["t000.00.jpg", "t001.50.jpg"]


def test_frame_names_sort_in_time_order(tmp_path):
    """Секунда дополняется нулями: иначе кадр 10-й секунды встаёт перед 2-й.

    Счётчик читает каталог, а каталог отдаёт имена по алфавиту. Лента,
    разложенная не по времени, превращает вход в выход — и это не видно.
    """
    written = write_packages([package(times=(2.0, 10.0, 100.0))],
                              tmp_path / "out" / "llm", "owlv2",
                              prompt="считайте", out_dir=tmp_path / "out")
    names = [p.name for p in written[0].frames]
    assert names == sorted(names)


def test_manifest_keeps_the_prompt_and_the_order_of_frames(tmp_path):
    """Слепой счётчик получает промпт и ленту кадров по времени, а не по имени."""
    written = write_packages([package(times=(0.0, 0.5, 10.0))],
                              tmp_path / "out" / "llm", "owlv2",
                              prompt="считайте людей", out_dir=tmp_path / "out")
    manifest = json.loads(written[0].manifest.read_text(encoding="utf-8"))
    assert manifest["prompt"] == "считайте людей"
    assert manifest["variant"] == "owlv2"
    assert manifest["door"] == 1
    assert [f["t"] for f in manifest["frames"]] == [0.0, 0.5, 10.0]


def test_door_without_frames_leaves_no_directory(tmp_path):
    """Дверь без кадров — это «не считали», а не пустой пакет с нулём."""
    written = write_packages([package(door=2, times=())], tmp_path / "out" / "llm",
                              "vlpart", prompt="считайте", out_dir=tmp_path / "out")
    assert written == []
    assert not (tmp_path / "out" / "llm" / "vlpart").exists()

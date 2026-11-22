import json
import shutil
import subprocess
from zipfile import ZipFile

from fastapi.testclient import TestClient

from shotforge import edit
from shotforge.models import Shot, ShotStatus
from shotforge.web.app import create_app


def approved_clip(project, shot_id, source):
    shot = project.shot("s01_pub", shot_id)
    shot.status = ShotStatus.video_approved
    project.save_shot("s01_pub", shot)
    shutil.copyfile(source, project.paths.shot("s01_pub", shot_id).approved_video)


def test_cut_refuses_unapproved_middle_shot_even_if_its_video_exists(project, mp4):
    clip = mp4(seconds=0.5)
    approved_clip(project, "sh01", clip)
    project.save_shot("s01_pub", Shot(id="sh03", duration_s=0.5))
    approved_clip(project, "sh03", clip)
    shutil.copyfile(clip, project.paths.shot("s01_pub", "sh02").approved_video)
    with TestClient(create_app(project.paths.root)) as client:
        response = client.get("/scene/s01_pub/cut")
        assert "sh02 is breakdown" in response.text
        response = client.post("/scene/s01_pub/cut", data={"end_title": "Fin", "end_seconds": "1"})
        assert response.status_code == 400
        assert "sh02 is breakdown" in response.text
        assert not (project.paths.edit / edit.VIDEO).exists()


def test_cut_exports_frame_accurate_reels_and_an_audible_bridge(project, mp4, tmp_path):
    outgoing = tmp_path / "tone.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(mp4(seconds=1.25)), "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=48000", "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", "-shortest", str(outgoing)],
        check=True,
    )
    approved_clip(project, "sh01", outgoing)
    approved_clip(project, "sh02", mp4("silent.mp4", seconds=0.75))

    with TestClient(create_app(project.paths.root)) as client:
        response = client.post("/scene/s01_pub/cut", data={
            "bridge-sh01": "0.25", "end_title": "100% Complete", "end_seconds": "0.5",
        })
        assert response.status_code == 200
        assert "Export EDL and reels" in response.text
        assert edit.read_settings(project, "s01_pub").bridges == {"sh01": 0.25}
        assert client.get("/scene/s01_pub/cut/rough_cut.mp4").status_code == 200
        assert client.get("/scene/s01_pub/cut/rough_cut.edl").status_code == 200

        folder = project.paths.edit
        output = subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=nb_read_frames,r_frame_rate", "-of", "json",
            str(folder / edit.VIDEO),
        ])
        video = json.loads(output)["streams"][0]
        assert video == {"r_frame_rate": "24/1", "nb_read_frames": "60"}
        edl = (folder / edit.EDL).read_text()
        assert "00:00:01:06 01:00:00:00 01:00:01:06" in edl
        assert "00:00:00:18 01:00:01:06 01:00:02:00" in edl
        assert "00:00:00:12 01:00:02:00 01:00:02:12" in edl

        with ZipFile(folder / edit.EXPORT) as archive:
            assert archive.namelist() == [
                edit.EDL, "reels/SH000001.mov", "reels/SH000002.mov", "reels/ENDCARD.mov",
            ]
            reel = archive.extract("reels/SH000002.mov", tmp_path)
        for offset, audible in (("0.02", True), ("0.35", False)):
            samples = subprocess.check_output([
                "ffmpeg", "-v", "error", "-ss", offset, "-i", str(reel), "-vn",
                "-t", "0.1", "-f", "s16le", "-",
            ])
            assert bool(samples.strip(b"\0")) is audible

        shot = project.shot("s01_pub", "sh02")
        shot.status = ShotStatus.video
        project.save_shot("s01_pub", shot)
        assert client.get("/scene/s01_pub/cut/rough_cut.zip").status_code == 404

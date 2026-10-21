import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile


class ExecutableJarTest(unittest.TestCase):
    def test_build_produces_standalone_executable_jar(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            shutil.copy(project / "build.sh", work)
            shutil.copytree(project / "src", work / "src")
            # Credentials must stay external even when present during the build.
            (work / "s3.properties").write_text("accessKey=private\nsecretKey=private\n")
            docker = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
                      "--mount", f"type=bind,src={work},dst=/work", "-w", "/work",
                      "eclipse-temurin:8-jdk"]
            subprocess.run(docker + ["sh", "build.sh"], check=True, capture_output=True, text=True, timeout=60)
            jar = work / "build" / "local-s3.jar"
            self.assertTrue(jar.is_file(), "build.sh must produce build/local-s3.jar")
            with zipfile.ZipFile(jar) as archive:
                self.assertIn("Main-Class: S3Server", archive.read("META-INF/MANIFEST.MF").decode())
                self.assertIn("S3Auth.class", archive.namelist())
                self.assertNotIn("s3.properties", archive.namelist())
            # Run the JAR alone, with no loose classes available in the working directory.
            shutil.rmtree(work / "build" / "classes")
            result = subprocess.run(docker + ["java", "-jar", "build/local-s3.jar"],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Usage:", result.stderr)
            self.assertNotIn("ClassNotFoundException", result.stderr)

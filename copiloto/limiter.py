"""
Teto de processador para os motores locais (voz e transcricao).
================================================================

Os motores locais (Piper = voz, whisper.cpp = transcricao) rodam SO na CPU -
os pacotes baixados sao os builds sem GPU (nada de CUDA/DirectML), entao nao
disputam a placa de video com o jogo. O que poderia roubar FPS e o processador,
e isso e limitado aqui com um TETO DURO do proprio Windows:

  - Job Object com JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP: o processo nunca passa
    de N% do processador TOTAL (ex.: 10%), mesmo que demore mais pra terminar;
  - prioridade ABAIXO DO NORMAL: o jogo sempre ganha a disputa;
  - o processo nasce SUSPENSO, entra no Job e so depois e liberado - nao existe
    nem um instante rodando sem o teto.

Fora do Windows (dev/teste) cai num Popen comum com `nice`.
"""

import ctypes
import os
import subprocess

DEFAULT_LIMIT = 10          # % do processador total

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
JobObjectCpuRateControlInformation = 15
JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4


class _CpuRate(ctypes.Structure):
    _fields_ = [("ControlFlags", ctypes.c_uint32), ("CpuRate", ctypes.c_uint32)]


class LimitedProcess:
    """Popen + o Job que o limita (o Job fecha junto com o processo)."""

    def __init__(self, proc, job=None):
        self.proc, self._job = proc, job

    def __getattr__(self, name):
        return getattr(self.proc, name)

    def close_job(self):
        if self._job:
            try:
                ctypes.windll.kernel32.CloseHandle(self._job)
            except Exception:
                pass
            self._job = None

    def __del__(self):
        self.close_job()


def _make_job(percent):
    k32 = ctypes.windll.kernel32
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise OSError("CreateJobObject falhou")
    info = _CpuRate(JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP,
                    max(1, min(10000, int(round(float(percent) * 100)))))   # centesimos de %
    ok = k32.SetInformationJobObject(ctypes.c_void_p(job), JobObjectCpuRateControlInformation,
                                     ctypes.byref(info), ctypes.sizeof(info))
    if not ok:
        k32.CloseHandle(ctypes.c_void_p(job))
        raise OSError(f"SetInformationJobObject falhou ({ctypes.GetLastError()})")
    return job


def popen(cmd, percent=DEFAULT_LIMIT, affinity=0, **kw):
    """subprocess.Popen com teto de CPU (percent <= 0 ou >= 100 = sem teto) e,
    opcional, presa aos nucleos de `affinity` (mascara de bits; 0 = todos)."""
    limited = bool(percent) and 0 < float(percent) < 100
    if os.name != "nt":
        if limited:
            kw.setdefault("preexec_fn", lambda: os.nice(10))
        return LimitedProcess(subprocess.Popen(cmd, **kw))

    flags = kw.pop("creationflags", 0) | CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
    if not limited and not affinity:
        return LimitedProcess(subprocess.Popen(cmd, creationflags=flags, **kw))

    proc = subprocess.Popen(cmd, creationflags=flags | CREATE_SUSPENDED, **kw)
    job = None
    k32 = ctypes.windll.kernel32
    h = ctypes.c_void_p(int(proc._handle))
    try:
        if affinity:
            k32.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            if not k32.SetProcessAffinityMask(h, ctypes.c_size_t(affinity)):
                print(f"[limite] afinidade recusada ({ctypes.GetLastError()})")
        if limited:
            job = _make_job(percent)
            if not k32.AssignProcessToJobObject(ctypes.c_void_p(job), h):
                raise OSError(f"AssignProcessToJobObject falhou ({ctypes.GetLastError()})")
    except Exception as e:
        print(f"[limite] sem teto de CPU ({e}) - seguindo so com prioridade baixa")
    finally:
        # libera o processo (estava suspenso) - com ou sem Job
        ctypes.windll.ntdll.NtResumeProcess(ctypes.c_void_p(int(proc._handle)))
    return LimitedProcess(proc, job)


def run(cmd, percent=DEFAULT_LIMIT, input=None, timeout=None, affinity=0, cwd=None):
    """Como subprocess.run (captura stdout/stderr em bytes), mas com teto de CPU."""
    p = popen(cmd, percent, affinity=affinity, cwd=cwd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = p.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
        raise
    finally:
        p.close_job()
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)

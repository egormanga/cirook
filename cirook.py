#!/usr/bin/env python3
# Cirook

import asyncio
import datetime
import io
import os
import shlex
import sys
import time
import typing
import tarfile

import aiodocker
import git
import pydantic
import yaml


class Step(pydantic.BaseModel):
	image: str = 'busybox'
	entrypoint: str = None
	init: list[str] = None
	args: str | list[str] = ''
	script: list[str] = []
	env: dict[str, str | int] | list[str] = None
	network: bool | typing.Literal['init'] = False


class Stage(pydantic.BaseModel):
	needs: list[str] = []
	steps: dict[str, Step]


class CI(pydantic.BaseModel):
	stages: dict[str, Stage]


async def print_cont_logs(cont, **kwargs):
	lines = str()
	async for entry in cont.log(stdout=True, stderr=True, follow=True, timestamps=True, **kwargs):
		lines += entry
		for line in lines.split('\n'):
			if (not line.endswith('\r')): break
			lines = lines.removeprefix(line).lstrip('\n')

			timestamp, _, line = line.partition(' ')
			print(f"\033[G\033[2;3m{datetime.datetime.fromisoformat(timestamp):%Y-%m-%d %H:%M:%S.%f}\033[23m │ \033[m", end='', file=sys.stderr, flush=True)
			print(line.removesuffix('\r'), flush=True)

	if (lines):
		timestamp, _, line = lines.partition(' ')
		print(f"\033[G\033[2;3m{datetime.datetime.fromisoformat(timestamp):%Y-%m-%d %H:%M:%S.%f}\033[23m │ \033[m", end='', file=sys.stderr, flush=True)
		print(line, flush=True)
		print('\033[G\033[2m⏎⃠\033[m', file=sys.stderr, flush=True)


async def main():
	async with aiodocker.Docker() as docker:
		repo = git.Repo()

		mode = os.path.basename(sys.argv[0])
		refs = (((sys.argv[2], sys.argv[3], sys.argv[1]),) if (mode == 'update') else tuple(map(str.split, sys.stdin)))

		if (mode == 'post-receive'):
			for old, new, ref in refs:
				print(f"\033[G\033[1m[\033[93mCI\033[39m: \033[3m{ref}\033[23m]\033[m", file=sys.stderr)

				commit = repo.commit(new)

				with io.BytesIO() as worktree:
					workdir = '/build'

					with tarfile.open(fileobj=worktree, mode='x') as tar:
						for blob in commit.tree.traverse():
							tarinfo = tarfile.TarInfo(os.path.join(workdir, blob.path))
							tarinfo.size = blob.size
							tarinfo.mode = blob.mode
							tar.addfile(tarinfo, blob.data_stream)
						tar.addfile(tarfile.TarInfo('/.init_done'))

					ci = CI.model_validate(yaml.safe_load((commit.tree / '.cirook.yml').data_stream))

					for name, stage in ci.stages.items():
						print(f"\033[G\033[1m[\033[92mStage\033[39m: {name}]\033[m", file=sys.stderr)

						for name, step in stage.steps.items():
							print(f"\033[G\033[1m[\033[94mStep\033[39m: {name}]\033[m", file=sys.stderr)

							print(f"\033[G\033[1m[Pull \033[96m{step.image}\033[39m]\033[m", file=sys.stderr)
							ii, lines, nl = 0, {}, False
							async for line in docker.images.pull(step.image, tag=(':' not in step.image and 'latest'), stream=True):
								try: id_ = line['id']
								except Exception:
									print(end=f"\033[{'GF'[nl]}", file=sys.stderr, flush=True); nl = False
									print(line['status'], flush=True); ii += 1
								else:
									try: lnoff = lines[id_]
									except KeyError:
										lnoff = lines[id_] = (ii or 1)
										print(file=sys.stderr); ii += 1

									print(f"\033[2K\033[{ii-lnoff+1}F\033[K{id_}: {line['status']}{(' '+line.get('progress', '')).rstrip()}\033[{ii-lnoff}E", file=sys.stderr, flush=True); nl = True
							image = await docker.images.inspect(step.image)

							try:
								print(f"\033[G\033[1m[Create \033[96m{step.image}\033[39m]\033[m", file=sys.stderr)
								cont = await docker.containers.create({
									'Image': step.image,
									'WorkingDir': workdir,
									'Entrypoint': ('sh', '-c'),
									'Cmd': ('\n'.join((("test -e /.init_done && exec -- " + shlex.join(
									                    (
									                    	*(image.get('Config', {}).get('Entrypoint', ())
									                    	  if (not step.entrypoint)
									                    	  else step.entrypoint
									                    	       if (not isinstance(step.entrypoint, str))
									                    	       else (step.entrypoint,)
									                    	 ),
									                    	*(step.args
									                    	  if (not isinstance(step.args, str))
									                    	  else shlex.split(step.args)
									                    	 ),
									                    )
									                    if (step.args)
									                    else ('sh', '-c', '\n'.join(step.script))
									                  )), *(step.init or ()))),),
									'Env': step.env,
									'NetworkDisabled': (not step.network),
									'AttachStdin': False,
									'AttachStdout': True,
									'AttachStderr': True,
									'Stream': True,
									'Tty': True,
									'AutoRemove': True,
								})

								if (step.init is not None):
									print(f"\033[G\033[1m[Init \033[96m{cont.id}\033[39m]\033[m", file=sys.stderr)
									await cont.start()

									await print_cont_logs(cont)

									res = await cont.wait()
									ec = res.get('StatusCode')
									print(f"\033[G\033[1{';91'*(ec != 0)}m[Exit code: {ec!r}]\033[m", file=sys.stderr)
									if (ec != 0): return

								print(f"\033[G\033[1m[Prepare \033[96m{cont.id}\033[39m]\033[m", file=sys.stderr)
								worktree.seek(0)
								await cont.put_archive('/', worktree)

								print(f"\033[G\033[1m[Run \033[96m{cont.id}\033[39m]\033[m", file=sys.stderr)
								started_at = time.time()
								await cont.start()
								await print_cont_logs(cont, since=started_at)

								res = await cont.wait()
								ec = res.get('StatusCode')
								print(f"\033[G\033[1{';91'*(ec != 0)}m[Exit code: {ec!r}]\033[m", file=sys.stderr)
								if (ec != 0): return
							except: raise
							else:
								worktree = (await cont.get_archive(workdir)).fileobj
							finally:
								try: await cont.delete(force=True)
								except NameError: pass


if (__name__ == '__main__'): exit(asyncio.run(main()))

# by Sdore, 2024
# cirook.sdore.me

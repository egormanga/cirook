# Cirook
(pronounced as _«see-rock»_; named after the similarity between English _«crook»_ and Russian _«крюк»_ meaning _«a hook»_.)

**CI/CD engine as a self-contained Git hook.**


## Example

```yaml
stages:
  lint:
    steps:
      shellcheck:
        image: shellcheck

  build:
    needs: []
    steps:
      compile:
        image: gcc
        script:
          - make

  test:
    needs: [build]
    steps:
      tets:
        script:
          - ./main | grep -qFx 'Hello world!'

  deploy:
    steps:
      scp:
        image: alpine
        network: init
        init:
          - apk add --no-cache openssh
        script:
          - scp './main' root@prod:/srv/bin/
```
_(Note: dicts are ordered.)_

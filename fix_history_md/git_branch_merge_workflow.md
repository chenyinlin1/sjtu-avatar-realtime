# Git 多人协作：本地开发分支合并到本地主分支流程

本文档记录一次较安全的 Git 合并流程，适用于以下分支关系：

```text
本地主分支：clean-main
开发分支：lgf-realtime
远程主分支：origin/main
远程开发分支：origin/lgf-realtime
```

核心思路是：

```text
先让开发分支 lgf-realtime 吸收主分支 clean-main 的最新代码，
在开发分支上解决冲突并完成测试，
确认无误后，再把开发分支合并回本地主分支 clean-main，
最后把本地主分支推送到远程主分支 main。
```

不建议直接使用 `--force` 覆盖远程主分支，除非非常确定远程主分支上的内容可以被覆盖。

---

## 1. 查看当前工作区状态

在开始合并之前，先确认当前工作区没有未提交的修改：

```bash
git status
```

如果看到类似下面的输出，说明工作区是干净的，可以继续：

```text
nothing to commit, working tree clean
```

如果有未提交内容，需要先提交或者暂存：

```bash
git add .
git commit -m "your commit message"
```

或者临时保存：

```bash
git stash
```

---

## 2. 切换到本地主分支 clean-main

```bash
git checkout clean-main
```

也可以使用新版本 Git 命令：

```bash
git switch clean-main
```

---

## 3. 更新本地主分支 clean-main

如果远程主分支叫 `main`，而本地主分支叫 `clean-main`，则执行：

```bash
git pull origin main
```

这一步的含义是：

```text
把远程 origin/main 的最新内容拉取并合并到本地 clean-main。
```

如果远程主分支也叫 `clean-main`，则可以执行：

```bash
git pull origin clean-main
```

---

## 4. 切回开发分支 lgf-realtime

```bash
git checkout lgf-realtime
```

或者：

```bash
git switch lgf-realtime
```

---

## 5. 先把主分支 clean-main 合并到开发分支 lgf-realtime

```bash
git merge clean-main
```

这一步的目的是：

```text
让开发分支 lgf-realtime 先吸收主分支 clean-main 的最新内容。
```

这样做的好处是：

1. 冲突先在开发分支上解决，不会直接影响主分支。
2. 可以在开发分支上完整测试代码。
3. 测试通过后，再合并回主分支会更安全。

---

## 6. 如果出现冲突，手动解决冲突

如果执行 `git merge clean-main` 后出现类似提示：

```text
CONFLICT (content): Merge conflict in xxx.py
Automatic merge failed; fix conflicts and then commit the result.
```

打开冲突文件，会看到类似内容：

```text
<<<<<<< HEAD
lgf-realtime 分支中的内容
=======
clean-main 分支中的内容
>>>>>>> clean-main
```

需要手动修改成最终希望保留的内容，然后执行：

```bash
git add .
git commit
```

如果 Git 自动生成了默认 merge commit 信息，可以直接保存退出。

---

## 7. 在开发分支上测试代码

合并主分支之后，建议在 `lgf-realtime` 分支上运行项目，确认代码可以正常工作。

例如：

```bash
# 根据项目实际情况执行
python main.py
```

或者：

```bash
# 根据项目实际情况执行
bash run.sh
```

确认没有问题后，再继续合并回主分支。

---

## 8. 切回本地主分支 clean-main

```bash
git checkout clean-main
```

或者：

```bash
git switch clean-main
```

---

## 9. 把开发分支 lgf-realtime 合并回 clean-main

推荐使用 `--no-ff`，这样可以保留一次明确的合并记录：

```bash
git merge --no-ff lgf-realtime -m "merge: lgf-realtime into clean-main"
```

也可以使用普通 merge：

```bash
git merge lgf-realtime
```

执行完成后，`lgf-realtime` 中的提交就已经合并到了本地 `clean-main`。

---

## 10. 查看合并结果

可以查看当前分支状态：

```bash
git status
```

也可以查看最近提交记录：

```bash
git log --oneline --graph --decorate -10
```

---

## 11. 推送本地主分支到远程主分支

如果本地主分支叫 `clean-main`，但远程主分支叫 `main`，执行：

```bash
git push origin clean-main:main
```

这条命令的含义是：

```text
把本地 clean-main 分支推送到远程 origin 的 main 分支。
```

如果远程主分支也叫 `clean-main`，执行：

```bash
git push origin clean-main
```

---

## 12. 完整推荐命令

如果你的情况是：

```text
本地主分支：clean-main
开发分支：lgf-realtime
远程主分支：origin/main
```

可以使用下面这组命令：

```bash
# 1. 确认工作区干净
git status

# 2. 切到本地主分支
git checkout clean-main

# 3. 更新本地主分支，让 clean-main 吸收远程 main 的最新内容
git pull origin main

# 4. 切回开发分支
git checkout lgf-realtime

# 5. 先把主分支合并到开发分支
git merge clean-main

# 如果出现冲突：
# 手动解决冲突后执行：
# git add .
# git commit

# 6. 在开发分支上测试代码
# 根据项目实际情况运行测试或启动命令

# 7. 测试通过后，切回主分支
git checkout clean-main

# 8. 把开发分支合并回主分支
git merge --no-ff lgf-realtime -m "merge: lgf-realtime into clean-main"

# 9. 推送本地主分支 clean-main 到远程 main
git push origin clean-main:main
```

---

## 13. 不建议使用 force push

不要轻易执行：

```bash
git push origin clean-main:main --force
```

原因是：

```text
--force 会强制覆盖远程 main，可能导致别人已经推送到远程的提交丢失。
```

多人协作时，除非非常明确远程分支应该被覆盖，否则应该优先使用普通合并和普通 push。

---

## 14. 推荐协作习惯

多人协作时，建议遵循下面的习惯：

```text
1. 主分支只保存稳定代码。
2. 每个人都在自己的开发分支上修改。
3. 开发分支完成后，先合并主分支最新代码并解决冲突。
4. 在开发分支上测试通过后，再合并回主分支。
5. 推送主分支时不要轻易使用 --force。
```

对应流程为：

```text
origin/main
    ↓
clean-main
    ↓
lgf-realtime 合并 clean-main 并测试
    ↓
clean-main 合并 lgf-realtime
    ↓
clean-main 推送到 origin/main
```

---

## 15. 本次流程总结

本次合并流程可以概括为：

```text
先同步主分支；
再让开发分支合并主分支；
在开发分支上解决冲突并测试；
确认成功后，再把开发分支合并回主分支；
最后推送主分支到远程。
```

也就是：

```text
clean-main → lgf-realtime → 测试 → clean-main → origin/main
```

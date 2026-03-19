# %% init-cell  # exec_order: 1
# [executed - do not modify]
import os
import shutil

desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
target_name = "新建文件夹"
deleted = []

for name in os.listdir(desktop):
    if target_name in name:
        path = os.path.join(desktop, name)
        if os.path.isdir(path):
            shutil.rmtree(path)
            deleted.append(name)

print(f"已删除 {len(deleted)} 个文件夹:", deleted)

# %% vscode-notebook-cell:/e%3A/projects/jupytest/test222.ipynb#W1sZmlsZQ%3D%3D  # exec_order: 1
# [executed - do not modify]
print(1)

# %% a26b9da8
# [pending - editable]
import os

desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".tif", ".svg"}

deleted = []
for f in os.listdir(desktop):
    path = os.path.join(desktop, f)
    if os.path.isfile(path) and os.path.splitext(f)[1].lower() in image_extensions:
        os.remove(path)
        deleted.append(f)

print(f"已删除 {len(deleted)} 个图片:", deleted)

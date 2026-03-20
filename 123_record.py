# %% 00e7c89d  # exec_order: 1
# [executed - do not modify]
import os
import csv

desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
csv_path = os.path.join(desktop, "sample_data.csv")

with open(csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["name", "age", "city"])
    writer.writerow(["Alice", 28, "Beijing"])
    writer.writerow(["Bob", 32, "Shanghai"])
    writer.writerow(["Charlie", 25, "Guangzhou"])

print(f"已生成示例 CSV: {csv_path}")

# %% 03f0303c  # exec_order: 2
# [executed - do not modify]
import pandas as pd

df = pd.read_csv(csv_path)
print(df)

# %% b5ac5aed  # exec_order: 3
# [executed - do not modify]
x = 42

# %% 7cd72a6e  # exec_order: 4
# [executed - do not modify]
x = 42

# %% ecf03c06  # exec_order: 5
# [executed - do not modify]
print(x)

# %% bbbdad69  # exec_order: 6
# [executed - do not modify]
print(x)

# %% 3af401b9  # exec_order: 7
# [executed - do not modify]
x = 42

# %% 66cc86e0  # exec_order: 8
# [executed - do not modify]
x = 42

# %% a1a298ee  # exec_order: 9
# [executed - do not modify]
x = 42

# %% cb417dda  # exec_order: 10
# [executed - do not modify]
print(x)

# %% 82b1c629  # exec_order: 11
# [executed - do not modify]
x = 42

# %% d6af4ea1  # exec_order: 12
# [executed - do not modify]
print(x)

# %% acc577ca  # exec_order: 13
# [executed - do not modify]
x = 42

# %% e4b0873a  # exec_order: 14
# [executed - do not modify]
x = 42

# %% 84ee349e  # exec_order: 15
# [executed - do not modify]
print(x)

# %% 28db1bab  # exec_order: 16
# [executed - do not modify]
x = 42

# %% 211c9d5b  # exec_order: 17
# [executed - do not modify]
print(x)

# %% 20662279  # exec_order: 18
# [executed - do not modify]
x = 42

# %% 2c9432d3  # exec_order: 19
# [executed - do not modify]
x = 42

# %% 4f1a63bd  # exec_order: 20
# [executed - do not modify]
print(x)

# %% c650e10a  # exec_order: 21
# [executed - do not modify]
x = 42

# %% 54c6df2f  # exec_order: 22
# [executed - do not modify]
print(x)

# %% 4dd3edfd  # exec_order: 23
# [executed - do not modify]
x = 42

# %% 31949800  # exec_order: 24
# [executed - do not modify]
x = 42

# %% a96adc60  # exec_order: 25
# [executed - do not modify]
print(x)

# %% 8d2050f4  # exec_order: 26
# [executed - do not modify]
print(x)

# %% 603723d2  # exec_order: 27
# [executed - do not modify]
x = 42

# %% c10ab206  # exec_order: 28
# [executed - do not modify]
x = 42

# %% cf77b08e  # exec_order: 29
# [executed - do not modify]
x = 42

# %% c4e67f74  # exec_order: 30
# [executed - do not modify]
print(x)

# %% fd6d7395  # exec_order: 31
# [executed - do not modify]
x = 42

# %% 1560bd7d  # exec_order: 32
# [executed - do not modify]
print(x)

# %% 64ae804f  # exec_order: 33
# [executed - do not modify]
x = 42

# %% 74722c79  # exec_order: 34
# [executed - do not modify]
x = 42

# %% 2024c766  # exec_order: 35
# [executed - do not modify]
print(x)

# %% 741830c2  # exec_order: 36
# [executed - do not modify]
x = 42

# %% 4dfcac8f  # exec_order: 37
# [executed - do not modify]
print(x)

# %% b359ffc7  # exec_order: 38
# [executed - do not modify]
x = 42

# %% a8089a73  # exec_order: 39
# [executed - do not modify]
x = 42

# %% 2a387c88  # exec_order: 40
# [executed - do not modify]
print(x)

# %% edec36d2  # exec_order: 41
# [executed - do not modify]
x = 42

# %% 83c3dbf7  # exec_order: 42
# [executed - do not modify]
print(x)

# %% 46597831  # exec_order: 43
# [executed - do not modify]
x = 42

# %% 8071f6c7  # exec_order: 44
# [executed - do not modify]
x = 42

# %% 0fae7383  # exec_order: 45
# [executed - do not modify]
print(x)

# %% 378fca5b  # exec_order: 46
# [executed - do not modify]
x = 42

# %% a42cc0b7  # exec_order: 47
# [executed - do not modify]
print(x)

# %% e10258f4  # exec_order: 48
# [executed - do not modify]
x = 42

# %% c0b9dc93  # exec_order: 49
# [executed - do not modify]
x = 42

# %% 01933717  # exec_order: 50
# [executed - do not modify]
print(x)

# %% 8cd626bb  # exec_order: 51
# [executed - do not modify]
x = 42

# %% 47c953d7  # exec_order: 52
# [executed - do not modify]
print(x)

# %% 54888b87
# [pending - editable]
import os

desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
csv_path = os.path.join(desktop, "sample_data.csv")
if os.path.exists(csv_path):
    os.remove(csv_path)
    print(f"已删除: {csv_path}")
else:
    print(f"文件不存在: {csv_path}")

# %% ed699ca4
# [pending - editable]
import os
import csv

desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
csv_path = os.path.join(desktop, "sample_data2.csv")

with open(csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["product", "price", "stock"])
    writer.writerow(["Apple", 5.5, 100])
    writer.writerow(["Banana", 3.2, 80])
    writer.writerow(["Orange", 4.0, 60])

print(f"已生成示例 CSV: {csv_path}")

# %% 4ada0f93
# [pending - editable]
import pandas as pd

df = pd.read_csv(csv_path)
print(df)

# %% d93b0a95
# [pending - editable]
with open("1.txt", "r", encoding="utf-8") as f:
    print(f.read())

# %% 4fcdf460
# [pending - editable]
import os

desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
csv_path = os.path.join(desktop, "sample_data2.csv")
if os.path.exists(csv_path):
    os.remove(csv_path)
    print(f"已删除: {csv_path}")
else:
    print(f"文件不存在: {csv_path}")

# %% 16a7a8c2
# [pending - editable]
print("你好")

# %% 782792d7
# [pending - editable]
print('hello world')

# %% b079c51f
# [pending - editable]
1/0

# %% 3cb7c3e8
# [empty - editable]

# %% 2998fb2d  # exec_order: 1
# [executed - do not modify]
x = 42

# %% 54df125d
# [pending - editable]
!jupyter kernelspec list

# %% 77e8d4d2
# [pending - editable]
print("MCP 执行测试")

# %% b4f94c95
# [pending - editable]
def prime_factors(n):
    """分解质因数"""
    factors = []
    d = 2
    while d * d <= n:
        while n % d == 0:
            factors.append(d)
            n //= d
        d += 1
    if n > 1:
        factors.append(n)
    return factors

# %% 43606d83
# [pending - editable]
print(f"{x} = {' × '.join(map(str, prime_factors(x)))}")

# %% [markdown] 36ccd814-19f5-4731-96bc-1f0b737affab
# [markdown - editable]
# # 这里做一些测试

# %% a7c124a8-9527-4354-bed2-41725310e72c
# [pending - editable]
a = 1

# %% ebdc345e-48cb-4580-81d3-d86587d65dfc
# [pending - editable]
b = 2

# %% 667edb6f-7b90-4361-8e0a-2921fd6e80f6
# [pending - editable]
print(123)

# %% a1ce4459-148d-493f-8d02-122fec04b67f
# [pending - editable]
print(a)

# %% 252bbbe2-27c1-477a-bae8-52c672a1d68b
# [pending - editable]
a+= 15

# %% 43a4e11a-613b-4873-b646-1c481403ab69
# [pending - editable]
## 报错测试

# %% 151c7beb-a6c0-486b-92d8-4e410b62b498
# [pending - editable]
1/0

# %% 0b2d95e3-a2f7-441a-9f82-ebb38ff2898a
# [pending - editable]
for i in range(5):
    a += 1

# %% 582d2cde-47e5-4004-8466-7f4d7bfac950
# [empty - editable]


# %% 38454632-9b40-4dab-9e25-b18be8ff0b4c
# [pending - editable]
with open('1.txt', 'r') as f:
    print(f.read())

# %% e1681b17-64a1-438e-a417-6ec06ad72f4f
# [pending - editable]
a = 0
b = 0

# %% b7919a92-a9e4-4dff-926e-f457ebb9cfbf
# [pending - editable]
print('hello from CLI')

# %% ccf4e1f8
# [empty - editable]


# %% 9e84e836-d312-4644-8dda-5bf8c951b032
# [pending - editable]
print(a)

# %% 0020b498
# [empty - editable]

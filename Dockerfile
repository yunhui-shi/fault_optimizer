# =========================================================================
# 阶段 1: "构建环境" (builder)
# 在这个阶段，我们安装所有开发工具和编译器，并将.py编译成.so二进制模块
# =========================================================================
FROM python:3.12-slim as builder

WORKDIR /build

RUN apt-get update && apt-get install -y build-essential

# 安装编译所需的依赖
RUN pip install cython setuptools wheel

# 复制源代码和构建脚本
COPY optimization_solver.py .
COPY setup.py .

# 运行编译命令，生成 .so 文件
# --inplace 会在当前目录生成二进制文件
RUN python setup.py build_ext --inplace

# 编译完成后，/build 目录下会有一个 optimization_solver.c 和一个 .so 文件
# 比如：optimization_solver.cpython-311-x86_64-linux-gnu.so


# =========================================================================
# 阶段 2: "最终运行环境" (final stage)
# 这是一个全新的、干净的、轻量的环境。我们只从 builder 阶段复制必要的东西。
# =========================================================================
FROM python:3.12-slim

WORKDIR /app

# 首先，确保你把 SCIPOptSuite-9.2.2-Linux-ubuntu24.sh 放在 Dockerfile 同目录下
COPY SCIPOptSuite-9.2.2-Linux-ubuntu24.sh /tmp/scip_installer.sh

# 给予执行权限并运行安装器
# 注意：你需要根据实际情况调整安装路径和是否接受许可协议
# 这里的 --prefix /usr/local/scip 指定了安装路径
# --skip-license 自动接受许可，如果安装器有这个选项的话
# 强烈建议你先在本地运行一次这个安装器，看看它需要哪些参数或交互
RUN chmod +x /tmp/scip_installer.sh \
    && /tmp/scip_installer.sh --prefix /usr/local/scip --skip-license \
    && rm /tmp/scip_installer.sh # 安装完成后清理安装器

# 将 SCIP 的可执行文件和库路径添加到环境变量中
# 请根据实际安装路径调整这里
ENV PATH="/usr/local/scip/bin:${PATH}"
ENV LD_LIBRARY_PATH="/usr/local/scip/lib:${LD_LIBRARY_PATH}"

# 1. 只安装运行时的依赖 (注意，这里不需要 cython 或 setuptools)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. 从 builder 阶段复制 FastAPI 的入口文件和编译好的二进制模块
COPY . .
RUN rm -f optimization_solver.py
# --- 关键步骤 ---
# 将 builder 阶段编译好的 .so 文件复制过来
COPY --from=builder /build/*.so .

# 3. 复制其他必要文件，比如 Pydantic 模型用到的文件（如果它们在别的文件里）

# 暴露端口并运行应用
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
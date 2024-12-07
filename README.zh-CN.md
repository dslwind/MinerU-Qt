# Miner-GUI
一个简单的 [MinerU](https://github.com/opendatalab/MinerU) 前端界面。

## 安装

1. 按照 MinerU 文档中的说明安装 MinerU：https://mineru.readthedocs.io/en/latest/user_guide/install/install.html

2. 假设您已经安装了 [Anaconda](https://docs.anaconda.com/anaconda/install/)，在终端中运行以下命令：
   ```bash
   conda create -n MinerU python=3.10
   conda activate MinerU
   python3 -m pip install -r requirements.txt
   # 如果您还没有下载模型权重文件，请先下载
   wget https://github.com/opendatalab/MinerU/raw/master/scripts/download_models_hf.py -O download_models_hf.py
   python3 download_models_hf.py
   ```

3. 启动程序：
   ```bash
   conda activate MinerU # 如果还没有激活环境，请运行此命令
   ./main.py # 或者
   python3 ./main.py
   ```

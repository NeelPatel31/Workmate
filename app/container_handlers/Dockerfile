FROM python:3.12-slim

ARG USER=agent
ARG UID=1000

# Install common Linux tools
RUN apt-get update && apt-get install -y \
    git \
    nano \
    wget \
    curl \
    unzip \
    zip \
    tar \
    gzip \
    xz-utils \
    procps \
    htop \
    tree \
    less \
    jq \
    file \
    poppler-utils \
    bubblewrap \
    && rm -rf /var/lib/apt/lists/*

# Common Python packages useful for AI agents
RUN pip install --no-cache-dir \
    # core data stack
    pandas numpy \
    # Excel / sheets
    openpyxl xlsxwriter xlrd \
    # Documents
    python-docx docx2txt python-pptx \
    # Images and media
    pillow \
    # PDF processing
    pymupdf pypdf reportlab pdfplumber \
    # Plotting and Visualization
    matplotlib seaborn plotly \
    # Web & Parsing
    beautifulsoup4 lxml \
    # Text & Markup
    markdown-it-py  pyyaml rich tqdm \
    # Data validation & config
    pydantic python-dotenv \
    # Utilities and ML Basics
    networkx python-magic

RUN groupadd --gid ${UID} ${USER} \
    && useradd --create-home --uid ${UID} --gid ${UID} --shell /bin/bash ${USER}

# /sandbox_data is a runtime bind mount (see docker-compose.yml); any dirs or
# permissions set here would be masked by the mount, so per-session dirs are
# created on the host and their ownership/mode is fixed at runtime by
# docker_client.provision_session.
WORKDIR /sandbox_data

USER ${USER}

CMD ["sleep", "infinity"]

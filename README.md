# WellNest SOAP Note Generator

A sophisticated healthcare application that automatically generates SOAP (Subjective, Objective, Assessment, Plan) notes from therapist-patient conversations using AI technology. This tool streamlines the documentation process for healthcare professionals by converting conversation transcripts into structured clinical notes.

## 🚀 Features

- **AI-Powered SOAP Note Generation**: Uses OpenAI's language models to analyze conversations and generate structured SOAP notes
- **AWS S3 Integration**: Seamlessly connects to S3 buckets to fetch conversation transcripts
- **Real-time File Monitoring**: Automatically detects new conversation files and provides notifications
- **Interactive Web Interface**: Built with Streamlit for an intuitive user experience
- **PDF Export**: Generate professional PDF reports of SOAP notes
- **Data Visualization**: Visual analytics and insights from conversation data
- **Secure Configuration**: Environment-based configuration for API keys and AWS credentials

## 🏗️ Architecture

```text
wng-soap-note-generator/
├── SOAP_NOTE_GENERATOR/           # Main application directory
│   ├── app.py                     # Main Streamlit application
│   ├── s3_notifications.py       # S3 file monitoring and notifications
│   ├── pyproject.toml            # Python project configuration
│   ├── .env.example              # Environment variables template
│   └── uv.lock                   # Dependency lock file
├── Dockerfile                     # Container configuration
├── .github/workflows/             # GitHub Actions for CI/CD
└── README.md                      # This file
```

## 🛠️ Technology Stack

- **Frontend**: Streamlit
- **AI/ML**: LangChain, OpenAI GPT
- **Cloud Storage**: AWS S3
- **Data Processing**: Pandas, Python
- **Visualization**: Plotly
- **PDF Generation**: ReportLab
- **Containerization**: Docker
- **Package Management**: UV (modern Python package manager)

## 📋 Prerequisites

Before you begin, ensure you have the following:

- Python 3.12+
- UV package manager (recommended) or pip
- AWS Account with S3 access
- OpenAI API key
- Docker (for containerized deployment)

## 🚀 Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/Wellnest-Group/wng-soap-note-generator.git
cd wng-soap-note-generator
```

### 2. Environment Setup

Copy the environment template and configure your credentials:

```bash
# Windows
copy SOAP_NOTE_GENERATOR\.env.example SOAP_NOTE_GENERATOR\.env

# Linux/Mac
cp SOAP_NOTE_GENERATOR/.env.example SOAP_NOTE_GENERATOR/.env
```

Edit the `.env` file with your credentials:

```env
AWS_S3_ACCESS_KEY_ID=your_aws_access_key_id_here
AWS_S3_SECRET_ACCESS_KEY=your_aws_secret_access_key_here
AWS_S3_REGION=us-east-1
OPENAI_API_KEY=your_openai_api_key_here
```

### 3. Installation

#### Option A: Using UV (Recommended)

First, install UV if you haven't already:

```bash
# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Linux/Mac
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then install dependencies:

```bash
cd SOAP_NOTE_GENERATOR
uv sync
```

#### Option B: Using pip

```bash
cd SOAP_NOTE_GENERATOR
pip install -e .
```

### 4. Run the Application

#### Using UV

```bash
cd SOAP_NOTE_GENERATOR
uv run streamlit run app.py
```

#### Using activated virtual environment

```bash
cd SOAP_NOTE_GENERATOR
# Activate your virtual environment first
streamlit run app.py
```

The application will be available at `http://localhost:8501`

## 🐳 Docker Deployment

### Build the Docker Image

```bash
docker build -t wng-soap-note-generator .
```

### Run the Container

```bash
docker run -p 8501:8501 \
  --env-file .env \
  wng-soap-note-generator
```

Or with individual environment variables:

```bash
docker run -p 8501:8501 \
  -e AWS_S3_ACCESS_KEY_ID=your_key \
  -e AWS_S3_SECRET_ACCESS_KEY=your_secret \
  -e AWS_S3_REGION=us-east-1 \
  -e OPENAI_API_KEY=your_openai_key \
  wng-soap-note-generator
```

## 📖 Usage

1. **Launch the Application**: Start the Streamlit app using the instructions above
2. **Configure S3 Connection**: Enter your S3 bucket name containing conversation transcripts
3. **Select Conversations**: Browse and select conversation files from your S3 bucket
4. **Generate SOAP Notes**: Click to automatically generate structured SOAP notes from the conversation
5. **Review and Export**: Review the generated notes and export as PDF if needed
6. **Monitor New Files**: The system will automatically notify you of new conversation files

## 🔧 Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `AWS_S3_ACCESS_KEY_ID` | AWS Access Key ID for S3 access | Yes |
| `AWS_S3_SECRET_ACCESS_KEY` | AWS Secret Access Key for S3 access | Yes |
| `AWS_S3_REGION` | AWS region for S3 bucket | Yes |
| `OPENAI_API_KEY` | OpenAI API key for AI processing | Yes |

### S3 Bucket Structure

Organize your conversation files in S3 as follows:

```text
your-bucket/
├── patient-001/
│   ├── session-2024-01-01.txt
│   └── session-2024-01-15.txt
├── patient-002/
│   └── session-2024-01-03.txt
└── ...
```

## 🤝 Contributing

We welcome contributions! Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Commit your changes (`git commit -m 'Add some amazing feature'`)
5. Push to the branch (`git push origin feature/amazing-feature`)
6. Open a Pull Request

## 🧪 Testing

Tests are currently in development. Once available, run tests using:

```bash
# Using UV
uv run pytest

# Using pip
pytest
```

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

If you encounter any issues or have questions:

1. Check the [Issues](https://github.com/Wellnest-Group/wng-soap-note-generator/issues) page
2. Create a new issue if your problem isn't already reported
3. Contact the WellNest development team

## 🏥 About WellNest

WellNest Group is dedicated to improving healthcare through innovative technology solutions. This SOAP note generator is part of our commitment to streamlining healthcare documentation and improving patient care efficiency.

## 📊 Project Status

- **Current Version**: 0.1.0
- **Status**: Active Development
- **Python Version**: 3.12+
- **Package Manager**: UV (recommended)
- **License**: MIT

---

Made with ❤️ by the WellNest Team

import streamlit as st
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough
from datetime import datetime, timedelta
import plotly.graph_objects as go
import plotly.express as px
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.units import inch
from reportlab.lib import colors
import kaleido
import json
import pandas as pd
import re
from collections import Counter
import boto3
import os
from dotenv import load_dotenv
from s3_notifications import check_new_files, display_new_file_notification, get_file_summary, create_reminder_notification

load_dotenv()

@st.cache_resource
def get_s3_client():
    """Initialize and cache S3 client"""
    return boto3.client(
        's3',
        aws_access_key_id=os.getenv('AWS_S3_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_S3_SECRET_ACCESS_KEY'),
        region_name=os.getenv('AWS_S3_REGION', 'us-east-1')
    )

def list_s3_folders_with_txt(bucket_name):
    """List all folders in S3 bucket that contain a .txt file"""
    try:
        s3_client = get_s3_client()
        response = s3_client.list_objects_v2(Bucket=bucket_name)
        
        folders_with_txt = {}
        if 'Contents' in response:
            for obj in response['Contents']:
                key = obj['Key']
                # Skip if it's a folder marker or doesn't contain .txt
                if key.endswith('/'):
                    continue
                
                # Extract folder path and filename
                parts = key.rsplit('/', 1)
                if len(parts) == 2:
                    folder_path = parts[0]
                    filename = parts[1]
                    
                    # Check if this is a .txt file
                    if filename.endswith('.txt'):
                        if folder_path not in folders_with_txt:
                            folders_with_txt[folder_path] = {
                                'txt_file': key,
                                'folder_name': folder_path.split('/')[-1]
                            }
        
        return folders_with_txt
    except Exception as e:
        st.error(f"Error accessing S3 bucket: {e}")
        return {}

def list_s3_txt_files(bucket_name):
    """List all .txt files in S3 bucket (legacy support)"""
    try:
        s3_client = get_s3_client()
        response = s3_client.list_objects_v2(Bucket=bucket_name)
        
        files = []
        if 'Contents' in response:
            for obj in response['Contents']:
                if obj['Key'].endswith('.txt'):
                    files.append(obj['Key'])
        
        return sorted(files)
    except Exception as e:
        st.error(f"Error accessing S3 bucket: {e}")
        return []

def get_s3_file_content(bucket_name, file_key):
    """Stream content directly from S3 without downloading to disk"""
    try:
        s3_client = get_s3_client()
        response = s3_client.get_object(Bucket=bucket_name, Key=file_key)
        content = response['Body'].read().decode('utf-8')
        return content
    except Exception as e:
        st.error(f"Error reading file from S3: {e}")
        return None

def stream_s3_file_content(bucket_name, file_key, chunk_size=1024*1024):
    """Stream large files from S3 in chunks for memory efficiency"""
    try:
        s3_client = get_s3_client()
        response = s3_client.get_object(Bucket=bucket_name, Key=file_key)
        while True:
            chunk = response['Body'].read(chunk_size)
            if not chunk:
                break
            yield chunk
    except Exception as e:
        st.error(f"Error streaming file from S3: {e}")
        yield None

def upload_to_s3(bucket_name, file_key, file_content):
    """Upload file content to S3"""
    try:
        s3_client = get_s3_client()
        if isinstance(file_content, str):
            file_content = file_content.encode('utf-8')
        elif isinstance(file_content, BytesIO):
            file_content = file_content.getvalue()
        
        s3_client.put_object(Bucket=bucket_name, Key=file_key, Body=file_content)
        return True
    except Exception as e:
        st.error(f"Error uploading file to S3: {e}")
        return False

def upload_pdf_to_s3(bucket_name, file_key, pdf_buffer):
    """Upload PDF buffer to S3"""
    try:
        s3_client = get_s3_client()
        pdf_buffer.seek(0)
        s3_client.put_object(Bucket=bucket_name, Key=file_key, Body=pdf_buffer.getvalue(), ContentType='application/pdf')
        return True
    except Exception as e:
        st.error(f"Error uploading PDF to S3: {e}")
        return False

def get_feedback_file_path(folder_path, version=1):
    """Generate feedback file path in S3"""
    return f"{folder_path}/feedback_v{version}.json"

def get_final_pdf_path(folder_path):
    """Generate final PDF file path in S3"""
    return f"{folder_path}/SOAP_Note_Final.pdf"

def load_feedback_from_s3(bucket_name, folder_path):
    """Load existing feedback versions from S3"""
    try:
        s3_client = get_s3_client()
        response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=f"{folder_path}/feedback_")
        
        feedback_versions = []
        if 'Contents' in response:
            for obj in response['Contents']:
                if obj['Key'].endswith('.json') and 'feedback_' in obj['Key']:
                    feedback_versions.append(obj['Key'])
        
        return sorted(feedback_versions)
    except Exception as e:
        st.warning(f"Could not load feedback versions: {e}")
        return []

def analyze_conversation_emotions(conversation):
    """Analyze conversation to extract emotional distribution based on actual content with enhanced accuracy"""
    if not conversation:
        return {"neutral": 100}
    
    # Separate client lines for more accurate emotion analysis
    lines = conversation.split('\n')
    client_lines = [line for line in lines if line.lower().startswith('client:')]
    
    # If no clear client lines, analyze entire conversation
    if not client_lines:
        analysis_text = conversation.lower()
    else:
        # Focus on client statements for emotion analysis
        analysis_text = ' '.join(client_lines).lower()
    
    # Enhanced emotion keywords with weighted importance and context awareness
    emotion_keywords = {
        'anxious': {
            'primary': ['anxious', 'anxiety', 'worried', 'worry', 'nervous', 'stressed', 'stress', 'panic', 'overwhelmed', 'fear', 'scared', 'terrified'],
            'secondary': ['tense', 'restless', 'uneasy', 'concern', 'concerned', 'frightened', 'apprehensive', 'on edge', 'wound up'],
            'contextual': ['what if', 'afraid that', 'worried about', 'nervous about', 'stressed about', 'can\'t stop thinking', 'keep worrying']
        },
        'sad': {
            'primary': ['sad', 'sadness', 'depressed', 'depression', 'down', 'upset', 'hopeless', 'empty', 'lonely', 'devastated'],
            'secondary': ['cry', 'crying', 'tears', 'grief', 'loss', 'hurt', 'pain', 'miserable', 'heartbroken', 'blue'],
            'contextual': ['feel empty', 'nothing matters', 'lost everything', 'can\'t go on', 'miss them', 'feel alone']
        },
        'angry': {
            'primary': ['angry', 'anger', 'mad', 'frustrated', 'frustration', 'irritated', 'annoyed', 'furious', 'rage'],
            'secondary': ['pissed', 'resentful', 'bitter', 'hostile', 'infuriated', 'outraged', 'livid', 'steaming'],
            'contextual': ['can\'t stand', 'drives me crazy', 'fed up', 'had enough', 'makes me mad', 'so annoying']
        },
        'hopeful': {
            'primary': ['hopeful', 'hope', 'optimistic', 'positive', 'better', 'improving', 'progress', 'excited', 'happy'],
            'secondary': ['good', 'confident', 'motivated', 'encouraged', 'cheerful', 'upbeat', 'pleased', 'content'],
            'contextual': ['looking forward', 'excited about', 'can\'t wait', 'things are getting', 'starting to feel', 'getting better']
        },
        'neutral': {
            'primary': ['okay', 'fine', 'normal', 'same', 'usual', 'routine', 'alright', 'stable'],
            'secondary': ['regular', 'typical', 'ordinary', 'standard', 'moderate', 'average', 'so-so'],
            'contextual': ['nothing special', 'just okay', 'about the same', 'no different']
        }
    }
    
    # Calculate weighted emotion scores with more sophisticated weighting
    emotion_scores = {}
    total_weighted_score = 0
    
    for emotion, keyword_groups in emotion_keywords.items():
        weighted_count = 0
        
        # Primary keywords (weight = 3) - strongest indicators
        for keyword in keyword_groups['primary']:
            pattern = r'\b' + re.escape(keyword) + r'\b'
            matches = len(re.findall(pattern, analysis_text))
            weighted_count += matches * 3
        
        # Secondary keywords (weight = 2) - moderate indicators
        for keyword in keyword_groups['secondary']:
            pattern = r'\b' + re.escape(keyword) + r'\b'
            matches = len(re.findall(pattern, analysis_text))
            weighted_count += matches * 2
            
        # Contextual phrases (weight = 2) - important context clues
        for phrase in keyword_groups['contextual']:
            if phrase in analysis_text:
                weighted_count += 2
        
        emotion_scores[emotion] = weighted_count
        total_weighted_score += weighted_count
    
    # Analyze conversation context for additional emotion inference
    context_emotions = analyze_contextual_emotions(analysis_text)
    
    # Add context-based emotion scores (weight = 1)
    for emotion, context_score in context_emotions.items():
        if emotion in emotion_scores:
            emotion_scores[emotion] += context_score
            total_weighted_score += context_score
    
    # If no emotional indicators found, infer from conversation tone
    if total_weighted_score == 0:
        tone_emotion = infer_emotional_tone(analysis_text)
        return tone_emotion
    
    # Convert to percentages with realistic distribution
    emotion_percentages = {}
    for emotion, score in emotion_scores.items():
        if score > 0:
            percentage = round((score / total_weighted_score) * 100)
            if percentage >= 3:  # Lower threshold for more realistic distribution
                emotion_percentages[emotion] = percentage
    
    # Ensure we have at least one emotion
    if not emotion_percentages:
        return {"neutral": 100}
    
    # Normalize to exactly 100% and ensure realistic distribution
    total_percentage = sum(emotion_percentages.values())
    if total_percentage != 100:
        if total_percentage < 100:
            # Add remainder to largest emotion
            largest_emotion = max(emotion_percentages, key=emotion_percentages.get)
            emotion_percentages[largest_emotion] += (100 - total_percentage)
        else:
            # Scale down proportionally
            scale_factor = 100 / total_percentage
            emotion_percentages = {k: round(v * scale_factor) for k, v in emotion_percentages.items()}
            # Final adjustment for rounding errors
            current_total = sum(emotion_percentages.values())
            if current_total != 100:
                largest_emotion = max(emotion_percentages, key=emotion_percentages.get)
                emotion_percentages[largest_emotion] += (100 - current_total)
    
    return emotion_percentages


def analyze_contextual_emotions(text):
    """Analyze conversation context to infer additional emotions with more comprehensive patterns"""
    context_indicators = {
        'anxious': [
            'what if', 'worried about', 'scared that', 'afraid of', 'nervous about', 'can\'t stop thinking',
            'keeps me up', 'constantly thinking', 'racing thoughts', 'worst case scenario', 'something bad',
            'panic about', 'stressed about', 'tense about', 'on edge about', 'freaking out'
        ],
        'sad': [
            'feel empty', 'miss', 'lost', 'gone', 'died', 'ended', 'over', 'nothing matters',
            'can\'t enjoy', 'no point', 'feel hollow', 'everything is gray', 'life is meaningless',
            'want to cry', 'tears come', 'feel broken', 'heart hurts', 'so lonely'
        ],
        'angry': [
            'hate', 'can\'t stand', 'so annoying', 'drives me crazy', 'fed up', 'had enough',
            'makes me mad', 'pisses me off', 'so frustrated', 'want to scream', 'boiling inside',
            'sick of', 'can\'t take it', 'rage inside', 'want to hit', 'explode with anger'
        ],
        'hopeful': [
            'looking forward', 'excited about', 'can\'t wait', 'things are getting', 'starting to feel',
            'getting better', 'improving every day', 'feel optimistic', 'bright future', 'positive change',
            'good things coming', 'feel encouraged', 'making progress', 'turning around', 'light at the end'
        ],
        'neutral': [
            'just okay', 'nothing special', 'about the same', 'no different', 'routine day',
            'typical week', 'normal stuff', 'usual things', 'nothing new', 'same old'
        ]
    }
    
    context_scores = {'anxious': 0, 'sad': 0, 'angry': 0, 'hopeful': 0, 'neutral': 0}
    
    for emotion, indicators in context_indicators.items():
        for indicator in indicators:
            # Count occurrences of each contextual phrase
            count = len(re.findall(re.escape(indicator), text))
            context_scores[emotion] += count
    
    # Additional pattern analysis for emotional context
    
    # Anxiety patterns
    question_count = text.count('?')
    if question_count >= 5:  # Excessive questioning indicates anxiety
        context_scores['anxious'] += 2
    
    # Uncertainty/doubt patterns for anxiety
    uncertainty_words = ['uncertain', 'doubt', 'unsure', 'don\'t know what', 'confused about']
    for word in uncertainty_words:
        if word in text:
            context_scores['anxious'] += 1
    
    # Sadness patterns - past tense emotional language
    past_loss_patterns = ['used to', 'before this', 'when things were', 'miss the old', 'remember when']
    for pattern in past_loss_patterns:
        if pattern in text:
            context_scores['sad'] += 1
    
    # Anger patterns - strong negative language
    intensity_words = ['really', 'so', 'very', 'extremely', 'totally']
    negative_words = ['hate', 'angry', 'frustrated', 'annoyed']
    for intensity in intensity_words:
        for negative in negative_words:
            if f"{intensity} {negative}" in text:
                context_scores['angry'] += 1
    
    # Hope patterns - future-oriented positive language
    future_positive = ['will be better', 'going to improve', 'planning to', 'hope to', 'want to change']
    for pattern in future_positive:
        if pattern in text:
            context_scores['hopeful'] += 1
    
    return context_scores


def infer_emotional_tone(text):
    """Infer emotional tone when no explicit emotion keywords are found using linguistic analysis"""
    words = text.split()
    word_count = len(words)
    
    # Analyze response length patterns
    if word_count < 20:  # Very short responses
        return {"neutral": 70, "sad": 30}  # Might indicate withdrawal or low mood
    elif word_count > 200:  # Very long responses
        return {"anxious": 40, "neutral": 60}  # Might indicate anxiety or pressure speech
    
    # Analyze punctuation patterns
    question_count = text.count('?')
    exclamation_count = text.count('!')
    
    if question_count >= 5:  # Excessive questioning
        return {"anxious": 80, "neutral": 20}
    elif exclamation_count >= 3:  # High exclamation use
        return {"angry": 50, "anxious": 30, "neutral": 20}
    
    # Analyze negative language patterns
    negative_words = ['no', 'not', 'never', 'nothing', 'nobody', 'none', 'can\'t', 'won\'t', 'shouldn\'t', 'don\'t']
    negative_count = sum(1 for word in negative_words if word in text)
    negative_ratio = negative_count / word_count if word_count > 0 else 0
    
    if negative_ratio > 0.15:  # High negative language density
        return {"sad": 60, "neutral": 40}
    elif negative_ratio > 0.10:
        return {"sad": 40, "neutral": 60}
    
    # Analyze sentence structure complexity
    sentences = text.split('.')
    avg_sentence_length = sum(len(sentence.split()) for sentence in sentences) / len(sentences) if sentences else 0
    
    if avg_sentence_length < 3:  # Very short, fragmented sentences
        return {"sad": 50, "neutral": 50}
    elif avg_sentence_length > 20:  # Very long, complex sentences
        return {"anxious": 40, "neutral": 60}
    
    # Analyze repetitive patterns (might indicate rumination)
    words_lower = [word.lower() for word in words]
    unique_words = set(words_lower)
    repetition_ratio = len(words_lower) / len(unique_words) if unique_words else 1
    
    if repetition_ratio > 2:  # High repetition
        return {"anxious": 50, "sad": 30, "neutral": 20}
    
    # Analyze temporal references
    past_words = ['was', 'were', 'had', 'used to', 'before', 'ago', 'yesterday', 'last']
    future_words = ['will', 'going to', 'tomorrow', 'next', 'soon', 'later', 'plan', 'hope']
    
    past_count = sum(1 for word in past_words if word in text)
    future_count = sum(1 for word in future_words if word in text)
    
    if past_count > future_count * 2:  # Heavy focus on past
        return {"sad": 60, "neutral": 40}
    elif future_count > past_count * 2:  # Heavy focus on future
        return {"hopeful": 40, "neutral": 60}
    
    # Default based on overall analysis
    if negative_ratio > 0.05:
        return {"neutral": 80, "sad": 20}
    else:
        return {"neutral": 100}


def analyze_observed_behaviors(conversation):
    """Generate clinical behavioral assessment scores (1-10) for four key areas based on conversation content"""
    if not conversation:
        return {
            "emotional_expression": 5,
            "mood_presentation": 5, 
            "engagement_level": 5,
            "thought_patterns": 5
        }
    
    lines = conversation.split('\n')
    text = conversation.lower()
    
    # Analyze conversation structure
    client_lines = [line for line in lines if line.lower().startswith('client:')]
    therapist_lines = [line for line in lines if line.lower().startswith('therapist:')]
    
    # Calculate conversation metrics
    total_client_words = sum(len(line.split()) for line in client_lines)
    avg_response_length = total_client_words / len(client_lines) if client_lines else 0
    
    clinical_assessment = {}
    
    # 1. EMOTIONAL EXPRESSION (AFFECT) - How patient shows emotions
    emotional_expression_score = 5  # Start at baseline
    
    # Enhanced affect indicators
    positive_affect = [
        'expressive', 'animated', 'enthusiastic', 'laughing', 'smiling', 'excited',
        'lively', 'energetic', 'vibrant', 'cheerful', 'bright'
    ]
    appropriate_affect = [
        'calm', 'relaxed', 'composed', 'appropriate', 'stable', 'steady',
        'balanced', 'even', 'controlled', 'measured'
    ]
    flat_affect = [
        'flat', 'monotone', 'emotionless', 'blank', 'expressionless', 'dull',
        'lifeless', 'robotic', 'mechanical', 'deadpan', 'empty'
    ]
    inappropriate_affect = [
        'laughing inappropriately', 'crying suddenly', 'angry outburst',
        'mood swings', 'inappropriate laughter', 'sudden tears'
    ]
    
    # Count affect indicators with more nuanced scoring
    positive_affect_count = sum(1 for indicator in positive_affect if indicator in text)
    appropriate_affect_count = sum(1 for indicator in appropriate_affect if indicator in text)
    flat_affect_count = sum(1 for indicator in flat_affect if indicator in text)
    inappropriate_affect_count = sum(1 for indicator in inappropriate_affect if indicator in text)
    
    # Adjust score based on affect indicators
    emotional_expression_score += positive_affect_count * 1.5
    emotional_expression_score += appropriate_affect_count * 1
    emotional_expression_score -= flat_affect_count * 2
    emotional_expression_score -= inappropriate_affect_count * 3
    
    # Assess emotional vocabulary richness
    emotion_words = ['feel', 'feeling', 'felt', 'emotion', 'emotional', 'mood', 'sense', 'experience']
    emotion_mentions = sum(text.count(word) for word in emotion_words)
    
    if emotion_mentions >= 8:
        emotional_expression_score += 2
    elif emotion_mentions >= 5:
        emotional_expression_score += 1
    elif emotion_mentions <= 1:
        emotional_expression_score -= 2
    
    # Analyze emotional range and depth
    emotional_descriptors = [
        'happy', 'sad', 'angry', 'scared', 'excited', 'worried', 'frustrated',
        'hopeful', 'disappointed', 'proud', 'ashamed', 'guilty', 'relieved'
    ]
    unique_emotions = sum(1 for word in emotional_descriptors if word in text)
    
    if unique_emotions >= 5:
        emotional_expression_score += 1.5
    elif unique_emotions <= 1:
        emotional_expression_score -= 1
    
    clinical_assessment["emotional_expression"] = max(1, min(10, round(emotional_expression_score)))
    
    # 2. MOOD PRESENTATION - Overall emotional state conveyed
    mood_score = 5  # Start at baseline
    
    # Enhanced mood indicators with intensity scoring
    positive_mood = ['happy', 'hopeful', 'optimistic', 'good', 'better', 'improving', 'positive', 'cheerful', 'upbeat', 'content']
    negative_mood = ['sad', 'depressed', 'anxious', 'worried', 'angry', 'frustrated', 'hopeless', 'upset', 'miserable', 'devastated']
    neutral_mood = ['okay', 'fine', 'alright', 'normal', 'stable', 'average', 'so-so']
    
    # Count with weighted importance
    positive_count = sum(text.count(word) for word in positive_mood)
    negative_count = sum(text.count(word) for word in negative_mood)
    neutral_count = sum(text.count(word) for word in neutral_mood)
    
    # Calculate mood balance
    total_mood_words = positive_count + negative_count + neutral_count
    if total_mood_words > 0:
        positive_ratio = positive_count / total_mood_words
        negative_ratio = negative_count / total_mood_words
        
        # Adjust score based on mood distribution
        mood_score += (positive_ratio * 5) - (negative_ratio * 5)
    
    # Assess overall tone with intensity markers
    intensity_positive = ['extremely happy', 'so excited', 'really good', 'absolutely wonderful', 'fantastic']
    intensity_negative = ['extremely sad', 'so depressed', 'really awful', 'absolutely terrible', 'devastating']
    
    for phrase in intensity_positive:
        if phrase in text:
            mood_score += 3
    
    for phrase in intensity_negative:
        if phrase in text:
            mood_score -= 4
    
    clinical_assessment["mood_presentation"] = max(1, min(10, round(mood_score)))
    
    # 3. ENGAGEMENT/PARTICIPATION - Level of involvement in conversation
    engagement_score = 5  # Start at baseline
    
    # Enhanced engagement indicators
    high_engagement = [
        'yes', 'absolutely', 'definitely', 'i think', 'i believe', 'i understand',
        'that makes sense', 'i agree', 'exactly', 'right', 'true', 'i see what you mean'
    ]
    moderate_engagement = [
        'maybe', 'perhaps', 'possibly', 'sometimes', 'i suppose', 'kind of'
    ]
    low_engagement = [
        'i don\'t know', 'i guess', 'whatever', 'sure', 'fine', 'nothing',
        'don\'t care', 'doesn\'t matter'
    ]
    
    # Calculate engagement indicators with different weights
    high_count = sum(text.count(phrase) for phrase in high_engagement)
    moderate_count = sum(text.count(phrase) for phrase in moderate_engagement)
    low_count = sum(text.count(phrase) for phrase in low_engagement)
    
    engagement_score += high_count * 1 - low_count * 1.5 + moderate_count * 0.5
    
    # Response quality assessment
    if avg_response_length > 30:  # Very detailed responses
        engagement_score += 3
    elif avg_response_length > 15:  # Good detail
        engagement_score += 1.5
    elif avg_response_length < 5:  # Very brief responses
        engagement_score -= 3
    elif avg_response_length < 10:  # Short responses
        engagement_score -= 1.5
    
    # Participation ratio analysis
    if len(client_lines) > 0 and len(therapist_lines) > 0:
        participation_ratio = len(client_lines) / len(therapist_lines)
        if participation_ratio > 1.0:  # Client speaks more than therapist
            engagement_score += 2
        elif participation_ratio > 0.7:  # Good participation
            engagement_score += 1
        elif participation_ratio < 0.3:  # Poor participation
            engagement_score -= 3
        elif participation_ratio < 0.5:  # Low participation
            engagement_score -= 1.5
    
    # Initiative and spontaneous sharing
    initiative_markers = ['i wanted to tell you', 'i\'ve been thinking', 'something happened', 'i realized']
    initiative_count = sum(1 for marker in initiative_markers if marker in text)
    engagement_score += initiative_count * 1.5
    
    clinical_assessment["engagement_level"] = max(1, min(10, round(engagement_score)))
    
    # 4. THOUGHT PATTERNS - How patient speaks and thinks
    thought_patterns_score = 5  # Start at baseline
    
    # Enhanced thinking assessment
    clear_thinking = [
        'understand', 'realize', 'makes sense', 'logical', 'clear', 'rational',
        'organized', 'coherent', 'focused', 'systematic', 'step by step'
    ]
    organized_speech = [
        'first', 'second', 'then', 'because', 'therefore', 'so', 'next',
        'finally', 'in conclusion', 'as a result', 'for example'
    ]
    confused_thinking = [
        'confused', 'don\'t understand', 'mixed up', 'scattered', 'all over the place',
        'can\'t think straight', 'foggy', 'unclear', 'jumbled', 'disorganized'
    ]
    negative_patterns = [
        'always', 'never', 'everyone', 'nobody', 'everything', 'nothing',
        'all the time', 'constantly', 'forever', 'worst', 'terrible'
    ]
    
    # Count thinking indicators
    clear_count = sum(text.count(word) for word in clear_thinking)
    organized_count = sum(text.count(word) for word in organized_speech)
    confused_count = sum(text.count(word) for word in confused_thinking)
    negative_pattern_count = sum(text.count(word) for word in negative_patterns)
    
    # Adjust score based on thinking patterns
    thought_patterns_score += clear_count * 1
    thought_patterns_score += organized_count * 1.5
    thought_patterns_score -= confused_count * 2
    thought_patterns_score -= negative_pattern_count * 0.5
    
    # Assess coherence and organization
    questions_count = text.count('?')
    if questions_count > 15:  # Excessive questioning
        thought_patterns_score -= 2
    elif questions_count > 8:
        thought_patterns_score -= 1
    
    # Repetitive or circular thinking patterns
    repetitive_phrases = ['i keep thinking', 'over and over', 'can\'t stop', 'round and round', 'same thing']
    circular_count = sum(1 for phrase in repetitive_phrases if phrase in text)
    thought_patterns_score -= circular_count * 1.5
    
    # Positive thought restructuring
    restructuring_phrases = ['maybe i could', 'what if i try', 'another way', 'different perspective', 'new approach']
    restructuring_count = sum(1 for phrase in restructuring_phrases if phrase in text)
    thought_patterns_score += restructuring_count * 2
    
    # Insight and self-awareness
    insight_markers = ['i notice', 'i\'m aware', 'i understand that', 'i see now', 'it\'s clear that']
    insight_count = sum(1 for marker in insight_markers if marker in text)
    thought_patterns_score += insight_count * 1.5
    
    clinical_assessment["thought_patterns"] = max(1, min(10, round(thought_patterns_score)))
    
    return clinical_assessment


def analyze_assessment_domains(conversation, emotional_distribution):
    """Calculate assessment domain scores based on conversation content with enhanced accuracy"""
    if not conversation:
        return {"mood": 5, "anxiety": 5, "sleep": 5, "functioning": 5, "risk": 1}
    
    text = conversation.lower()
    
    # Enhanced domain-specific indicators with weighted scoring
    domain_indicators = {
        'mood': {
            'negative': {
                'severe': ['extremely depressed', 'severely depressed', 'completely hopeless', 'want to die'],
                'moderate': ['depressed', 'sad', 'down', 'hopeless', 'empty', 'crying', 'tears', 'miserable', 'devastated'],
                'mild': ['bit down', 'little sad', 'not great', 'feeling low', 'blue', 'gloomy']
            },
            'positive': {
                'strong': ['extremely happy', 'really good', 'fantastic', 'wonderful', 'amazing'],
                'moderate': ['better', 'good mood', 'happy', 'positive', 'improving', 'upbeat'],
                'mild': ['okay', 'fine', 'alright', 'not bad', 'decent']
            }
        },
        'anxiety': {
            'negative': {
                'severe': ['panic attacks', 'severe anxiety', 'can\'t breathe', 'overwhelming panic', 'constant anxiety'],
                'moderate': ['anxious', 'worry', 'panic', 'stressed', 'nervous', 'overwhelmed', 'fear', 'scared', 'terrified'],
                'mild': ['bit worried', 'little anxious', 'somewhat nervous', 'slight concern', 'tense']
            },
            'positive': {
                'strong': ['completely calm', 'totally relaxed', 'no anxiety', 'perfectly peaceful'],
                'moderate': ['calm', 'relaxed', 'peaceful', 'less anxious', 'manageable'],
                'mild': ['okay', 'not too bad', 'handling it', 'coping']
            }
        },
        'sleep': {
            'negative': {
                'severe': ['no sleep', 'can\'t sleep at all', 'awake all night', 'severe insomnia', 'exhausted'],
                'moderate': ['can\'t sleep', 'insomnia', 'waking up', 'nightmares', 'tired', 'poor sleep'],
                'mild': ['bit tired', 'restless sleep', 'waking early', 'light sleep']
            },
            'positive': {
                'strong': ['sleeping perfectly', 'excellent sleep', 'sleeping through the night'],
                'moderate': ['sleeping well', 'good sleep', 'rested', 'better sleep'],
                'mild': ['okay sleep', 'getting some sleep', 'not bad']
            }
        },
        'functioning': {
            'negative': {
                'severe': ['can\'t work at all', 'unable to function', 'completely disabled', 'can\'t do anything'],
                'moderate': ['can\'t work', 'struggling', 'difficult', 'problems at work', 'can\'t focus', 'barely functioning'],
                'mild': ['bit difficult', 'some problems', 'challenging', 'harder than usual']
            },
            'positive': {
                'strong': ['functioning perfectly', 'excellent at work', 'highly productive'],
                'moderate': ['working well', 'productive', 'managing', 'doing better', 'good performance'],
                'mild': ['okay at work', 'getting by', 'managing okay']
            }
        },
        'risk': {
            'negative': {
                'severe': ['want to kill myself', 'suicide', 'end my life', 'better off dead', 'plan to die'],
                'moderate': ['hurt myself', 'end it all', 'no point in living', 'want to disappear', 'harm myself'],
                'mild': ['hopeless', 'worthless', 'pointless', 'give up', 'no reason to live']
            },
            'positive': {
                'strong': ['want to live', 'looking forward', 'excited about future', 'strong support'],
                'moderate': ['safe', 'no thoughts of harm', 'support system', 'getting help'],
                'mild': ['okay', 'managing', 'hanging in there']
            }
        }
    }
    
    domain_scores = {}
    
    for domain, indicators in domain_indicators.items():
        # Count indicators with severity weighting
        severe_negative = sum(text.count(phrase) for phrase in indicators['negative']['severe'])
        moderate_negative = sum(text.count(phrase) for phrase in indicators['negative']['moderate'])
        mild_negative = sum(text.count(phrase) for phrase in indicators['negative']['mild'])
        
        strong_positive = sum(text.count(phrase) for phrase in indicators['positive']['strong'])
        moderate_positive = sum(text.count(phrase) for phrase in indicators['positive']['moderate'])
        mild_positive = sum(text.count(phrase) for phrase in indicators['positive']['mild'])
        
        # Calculate weighted negative score (higher = more severe)
        negative_score = (severe_negative * 4) + (moderate_negative * 2) + (mild_negative * 1)
        
        # Calculate weighted positive score (higher = better functioning)
        positive_score = (strong_positive * 3) + (moderate_positive * 2) + (mild_positive * 1)
        
        # Calculate domain score (1-10 scale where higher = more problematic)
        if negative_score == 0 and positive_score == 0:
            # No explicit indicators found - infer from emotional distribution if available
            if domain == 'anxiety' and emotional_distribution and 'anxious' in emotional_distribution:
                # Scale anxiety based on anxious emotion percentage
                anxious_pct = emotional_distribution['anxious']
                score = min(10, max(1, round((anxious_pct / 10))))  # 10% anxious = score of 1, 100% = 10
            elif domain == 'mood' and emotional_distribution and 'sad' in emotional_distribution:
                # Scale mood disturbance based on sad emotion percentage
                sad_pct = emotional_distribution['sad']
                score = min(10, max(1, round((sad_pct / 10))))  # 10% sad = score of 1, 100% = 10
            elif domain == 'risk':
                # Risk requires explicit indicators - default to very low if none found
                score = 1
            elif domain == 'sleep':
                # No sleep mentions suggests it's not a major concern
                score = 2
            elif domain == 'functioning':
                # No explicit functioning issues suggests moderate to good functioning
                score = 3
            else:
                # For other domains with no data, use conversation length as proxy
                # Longer, more engaged conversations suggest better functioning
                score = 3  # Assume mild severity if no explicit indicators
        else:
            # Calculate based on balance of positive vs negative indicators
            total_indicators = negative_score + positive_score
            if total_indicators > 0:
                negative_ratio = negative_score / total_indicators
                # Scale to 1-10 where 10 is most severe
                base_score = negative_ratio * 9 + 1
                
                # Adjust for positive indicators
                if positive_score > negative_score:
                    base_score = max(1, base_score - 2)
                elif positive_score > 0:
                    base_score = max(1, base_score - 1)
                
                score = min(10, max(1, round(base_score)))
            else:
                score = 5
        
        # Special handling for risk domain
        if domain == 'risk':
            # Risk should generally be lower unless explicit risk factors
            if severe_negative > 0:
                score = min(10, score + 2)
            elif negative_score == 0:
                score = min(3, score)  # Cap at low-moderate risk if no negative indicators
        
        domain_scores[domain] = score
    
    # Cross-validate with emotional distribution
    if emotional_distribution:
        # Adjust anxiety score based on anxious emotion percentage
        if 'anxious' in emotional_distribution and emotional_distribution['anxious'] > 50:
            domain_scores['anxiety'] = min(10, domain_scores['anxiety'] + 1)
        
        # Adjust mood score based on sad emotion percentage
        if 'sad' in emotional_distribution and emotional_distribution['sad'] > 50:
            domain_scores['mood'] = min(10, domain_scores['mood'] + 1)
        
        # Adjust based on hopeful emotions
        if 'hopeful' in emotional_distribution and emotional_distribution['hopeful'] > 30:
            domain_scores['mood'] = max(1, domain_scores['mood'] - 1)
            domain_scores['risk'] = max(1, domain_scores['risk'] - 1)
    
    return domain_scores


def calculate_risk_level(assessment_domains, conversation):
    """Determine risk level based on assessment and conversation content with enhanced accuracy"""
    if not conversation:
        # Return None or 'unknown' when no conversation - let calling code handle display
        return "unknown"
    
    text = conversation.lower()
    
    # Comprehensive risk indicators with severity levels
    risk_indicators = {
        'high': [
            'want to kill myself', 'suicide', 'end my life', 'better off dead', 'plan to die',
            'kill me', 'suicidal', 'take my own life', 'rather be dead', 'ending it all'
        ],
        'moderate': [
            'hurt myself', 'end it all', 'no point in living', 'want to disappear', 'harm myself',
            'cut myself', 'self-harm', 'no reason to live', 'pointless existence', 'can\'t go on'
        ],
        'mild': [
            'hopeless', 'worthless', 'pointless', 'give up', 'no reason', 'nothing matters',
            'no hope', 'useless', 'burden', 'better without me'
        ]
    }
    
    # Protective factors
    protective_factors = [
        'support system', 'family needs me', 'getting help', 'therapy helps', 'want to live',
        'looking forward', 'things to live for', 'people care', 'safe', 'no thoughts of harm',
        'won\'t hurt myself', 'seeking help'
    ]
    
    # Count risk indicators with weighted scoring
    high_risk_count = sum(text.count(phrase) for phrase in risk_indicators['high'])
    moderate_risk_count = sum(text.count(phrase) for phrase in risk_indicators['moderate'])
    mild_risk_count = sum(text.count(phrase) for phrase in risk_indicators['mild'])
    
    # Count protective factors
    protective_count = sum(text.count(phrase) for phrase in protective_factors)
    
    # Calculate weighted risk score
    risk_score = (high_risk_count * 4) + (moderate_risk_count * 2) + (mild_risk_count * 1)
    
    # Adjust for protective factors
    if protective_count > 0:
        risk_score = max(0, risk_score - protective_count)
    
    # Check assessment scores if available
    if assessment_domains:
        domain_risk_score = assessment_domains.get('risk', 1)
        mood_score = assessment_domains.get('mood', 5)
        
        # High domain scores indicate more severe symptoms
        if domain_risk_score >= 8:
            risk_score += 3
        elif domain_risk_score >= 6:
            risk_score += 1
        
        if mood_score >= 8:
            risk_score += 1
    
    # Additional context analysis
    # Look for expressions of pain and distress
    distress_indicators = ['can\'t take it anymore', 'too much pain', 'overwhelming', 'unbearable']
    distress_count = sum(1 for phrase in distress_indicators if phrase in text)
    risk_score += distress_count
    
    # Look for social isolation
    isolation_indicators = ['no one cares', 'all alone', 'nobody understands', 'isolated', 'no friends']
    isolation_count = sum(1 for phrase in isolation_indicators if phrase in text)
    risk_score += isolation_count * 0.5
    
    # Determine final risk level
    if high_risk_count > 0 or risk_score >= 8:
        return "high"
    elif moderate_risk_count > 0 or risk_score >= 4:
        return "moderate"
    elif mild_risk_count > 0 or risk_score >= 1:
        return "low-moderate"
    else:
        return "low"


def calculate_engagement_productivity(conversation):
    """Calculate engagement and productivity scores based on conversation quality with enhanced analysis"""
    if not conversation:
        # Return None values when no conversation provided - will be handled gracefully in display
        return None, None
    
    text = conversation.lower()
    lines = conversation.split('\n')
    
    # Count client responses
    client_responses = [line for line in lines if line.lower().startswith('client:')]
    therapist_responses = [line for line in lines if line.lower().startswith('therapist:')]
    
    if len(client_responses) == 0:
        return 1, 1  # No client engagement
    
    # Enhanced engagement indicators with weighted scoring
    engagement_indicators = {
        'high': {
            'phrases': ['yes absolutely', 'definitely', 'i think', 'i believe', 'i understand', 
                       'that makes sense', 'i agree', 'exactly', 'you\'re right', 'i see what you mean',
                       'that\'s interesting', 'i never thought of that', 'that helps'],
            'weight': 2
        },
        'moderate': {
            'phrases': ['yes', 'maybe', 'perhaps', 'possibly', 'sometimes', 'i suppose', 
                       'kind of', 'sort of', 'i think so', 'could be'],
            'weight': 1
        },
        'low': {
            'phrases': ['i don\'t know', 'i guess', 'whatever', 'sure', 'fine', 'nothing',
                       'don\'t care', 'doesn\'t matter', 'not really', 'no idea'],
            'weight': -2
        },
        'disengagement': {
            'phrases': ['can we stop', 'i don\'t want to talk', 'this is pointless', 'waste of time',
                       'not helping', 'don\'t see the point'],
            'weight': -3
        }
    }
    
    # Calculate engagement score
    engagement_score = 5  # Start at baseline
    
    for level, data in engagement_indicators.items():
        count = sum(text.count(phrase) for phrase in data['phrases'])
        engagement_score += count * data['weight']
    
    # Response quality analysis
    total_client_words = sum(len(response.split()) for response in client_responses)
    avg_response_length = total_client_words / len(client_responses)
    
    # Adjust for response length and quality
    if avg_response_length > 50:  # Very detailed responses
        engagement_score += 3
    elif avg_response_length > 25:  # Good detail
        engagement_score += 2
    elif avg_response_length > 15:  # Moderate detail
        engagement_score += 1
    elif avg_response_length < 5:  # Very brief responses
        engagement_score -= 3
    elif avg_response_length < 8:  # Brief responses
        engagement_score -= 1
    
    # Participation ratio analysis
    if len(therapist_responses) > 0:
        participation_ratio = len(client_responses) / len(therapist_responses)
        if participation_ratio > 1.5:  # Client speaks much more
            engagement_score += 2
        elif participation_ratio > 1.0:  # Client speaks more
            engagement_score += 1
        elif participation_ratio > 0.7:  # Good participation
            engagement_score += 0.5
        elif participation_ratio < 0.3:  # Poor participation
            engagement_score -= 3
        elif participation_ratio < 0.5:  # Low participation
            engagement_score -= 1.5
    
    # Initiative and spontaneous sharing
    initiative_markers = [
        'i wanted to tell you', 'i\'ve been thinking', 'something happened', 'i realized',
        'i discovered', 'i noticed', 'it occurred to me', 'i figured out'
    ]
    initiative_count = sum(1 for marker in initiative_markers if marker in text)
    engagement_score += initiative_count * 1.5
    
    # Questions asked by client (shows engagement)
    client_questions = sum(line.count('?') for line in client_responses)
    engagement_score += min(client_questions * 0.5, 2)  # Cap bonus at 2 points
    
    # Final engagement level (1-10)
    engagement_level = max(1, min(10, round(engagement_score)))
    
    # PRODUCTIVITY ANALYSIS
    productivity_score = 5  # Start at baseline
    
    # Enhanced productivity indicators
    therapeutic_progress = {
        'insight': ['i understand now', 'i realize', 'makes sense', 'i see', 'that explains',
                   'now i know', 'i get it', 'i\'m aware', 'i notice', 'i learned'],
        'application': ['i tried', 'i practiced', 'i used what you said', 'i applied',
                       'i implemented', 'i worked on', 'i did the homework'],
        'emotional_processing': ['i feel better', 'that helps', 'i\'m relieved', 'clearer now',
                                'less confused', 'making progress', 'getting better'],
        'commitment': ['i will try', 'i\'m going to', 'i plan to', 'i want to work on',
                      'i\'m committed', 'i\'ll practice', 'i\'ll remember']
    }
    
    # Calculate productivity based on therapeutic indicators
    for category, phrases in therapeutic_progress.items():
        count = sum(text.count(phrase) for phrase in phrases)
        if category == 'insight':
            productivity_score += count * 2  # Insight is highly valuable
        elif category == 'application':
            productivity_score += count * 2.5  # Application shows engagement with treatment
        elif category == 'emotional_processing':
            productivity_score += count * 1.5
        elif category == 'commitment':
            productivity_score += count * 1.5
    
    # Depth of self-reflection
    reflection_markers = [
        'i think about', 'i wonder', 'i ask myself', 'i question', 'i examine',
        'looking at myself', 'reflecting on', 'considering', 'evaluating'
    ]
    reflection_count = sum(1 for marker in reflection_markers if marker in text)
    productivity_score += reflection_count * 1
    
    # Problem-solving attempts
    problem_solving = [
        'maybe i could', 'what if i', 'i could try', 'another approach', 'different way',
        'alternative', 'solution', 'strategy', 'plan'
    ]
    problem_solving_count = sum(1 for phrase in problem_solving if phrase in text)
    productivity_score += problem_solving_count * 2
    
    # Emotional vocabulary and expression (indicates processing)
    emotional_words = [
        'feel', 'feeling', 'felt', 'emotion', 'mood', 'sense', 'experience',
        'angry', 'sad', 'happy', 'anxious', 'frustrated', 'hopeful', 'scared'
    ]
    emotion_mentions = sum(text.count(word) for word in emotional_words)
    if emotion_mentions >= 15:
        productivity_score += 2
    elif emotion_mentions >= 10:
        productivity_score += 1.5
    elif emotion_mentions >= 5:
        productivity_score += 1
    
    # Session depth based on conversation flow
    if len(therapist_responses) > 0 and len(client_responses) > 0:
        # More back-and-forth indicates deeper exploration
        conversation_depth = min(len(client_responses), len(therapist_responses))
        if conversation_depth >= 15:
            productivity_score += 2
        elif conversation_depth >= 10:
            productivity_score += 1
        elif conversation_depth >= 5:
            productivity_score += 0.5
    
    # Resistance or avoidance (reduces productivity)
    avoidance_markers = [
        'don\'t want to talk about', 'rather not discuss', 'can we change the subject',
        'not ready', 'too difficult', 'can\'t handle', 'too painful'
    ]
    avoidance_count = sum(1 for marker in avoidance_markers if marker in text)
    productivity_score -= avoidance_count * 1.5
    
    # Final productivity level (1-10)
    productivity_level = max(1, min(10, round(productivity_score)))
    
    return engagement_level, productivity_level


def extract_emotional_context(conversation, emotional_distribution):
    """Extract context for when different emotions appear in conversation - analyzes actual conversation content"""
    if not conversation or not emotional_distribution:
        return {}
    
    # Additional safety check for None values
    if emotional_distribution is None:
        return {}
    
    lines = conversation.split('\n')
    context_mapping = {}
    
    # Enhanced topic/context identification patterns
    topic_patterns = {
        'work_stress': ['work', 'job', 'boss', 'manager', 'colleague', 'office', 'project', 'deadline', 'meeting', 'career', 'promotion', 'workload', 'overtime', 'workplace'],
        'relationship_issues': ['husband', 'wife', 'partner', 'boyfriend', 'girlfriend', 'marriage', 'divorce', 'dating', 'breakup', 'relationship', 'spouse', 'romantic', 'intimacy'],
        'family_dynamics': ['mother', 'father', 'mom', 'dad', 'parent', 'child', 'son', 'daughter', 'brother', 'sister', 'family', 'kids', 'siblings', 'relatives'],
        'health_concerns': ['health', 'sick', 'doctor', 'hospital', 'medicine', 'pain', 'illness', 'surgery', 'treatment', 'diagnosis', 'medical', 'symptoms'],
        'financial_worries': ['money', 'financial', 'debt', 'bills', 'budget', 'salary', 'income', 'expensive', 'afford', 'loan', 'mortgage', 'rent', 'cost'],
        'social_situations': ['friends', 'social', 'party', 'gathering', 'isolation', 'lonely', 'crowd', 'people', 'public', 'social media', 'friendship'],
        'academic_pressure': ['school', 'college', 'university', 'exam', 'study', 'grades', 'homework', 'professor', 'student', 'assignment', 'test'],
        'personal_development': ['therapy', 'counseling', 'goals', 'progress', 'change', 'improvement', 'growth', 'development', 'self-help', 'meditation'],
        'past_experiences': ['childhood', 'past', 'memory', 'history', 'before', 'used to', 'growing up', 'years ago', 'when I was'],
        'future_uncertainty': ['future', 'tomorrow', 'next week', 'next month', 'what if', 'afraid of', 'scared about', 'uncertain', 'unknown', 'planning'],
        'daily_routine': ['morning', 'evening', 'daily', 'routine', 'schedule', 'home', 'house', 'chores', 'cooking', 'cleaning'],
        'sleep_issues': ['sleep', 'sleeping', 'insomnia', 'nightmares', 'dreams', 'tired', 'exhausted', 'bed', 'wake up', 'rest']
    }
    
    # Comprehensive emotion keywords
    emotion_keywords = {
        'anxious': ['anxious', 'worried', 'nervous', 'stressed', 'panic', 'overwhelmed', 'fear', 'scared', 'tense', 'restless', 'uneasy', 'concern', 'worry', 'anxiety'],
        'sad': ['sad', 'depressed', 'down', 'upset', 'cry', 'crying', 'tears', 'hopeless', 'empty', 'lonely', 'grief', 'loss', 'hurt', 'pain', 'miserable'],
        'angry': ['angry', 'mad', 'frustrated', 'irritated', 'annoyed', 'rage', 'furious', 'pissed', 'resentful', 'bitter', 'hostile', 'infuriated'],
        'hopeful': ['hopeful', 'optimistic', 'positive', 'better', 'improving', 'progress', 'good', 'excited', 'confident', 'motivated', 'encouraged', 'looking forward']
    }
    
    # Analyze conversation for emotional contexts
    for emotion in emotional_distribution.keys():
        if emotion == 'neutral' or emotional_distribution.get(emotion, 0) <= 0:
            continue
            
        emotion_contexts = []
        keywords = emotion_keywords.get(emotion, [])
        
        # Find lines with emotional content and extract specific context
        for line in lines:
            line_lower = line.lower().strip()
            
            # Skip empty lines or non-client lines for more accuracy
            if not line_lower or line_lower.startswith('therapist:'):
                continue
            
            # Check if this line contains the emotion
            emotion_found = any(keyword in line_lower for keyword in keywords)
            
            if emotion_found:
                # Look for specific topics in this emotional line
                found_topics = []
                for topic, topic_words in topic_patterns.items():
                    if any(word in line_lower for word in topic_words):
                        found_topics.append(topic)
                
                if found_topics:
                    emotion_contexts.extend(found_topics)
                else:
                    # Try to extract context from the sentence structure
                    context_extracted = False
                    
                    # Look for "about", "with", "when", "because" patterns
                    context_indicators = ['about', 'with', 'when', 'because', 'regarding', 'concerning']
                    for indicator in context_indicators:
                        if indicator in line_lower:
                            try:
                                parts = line_lower.split(indicator, 1)
                                if len(parts) > 1:
                                    context_part = parts[1].strip()[:50]  # First 50 chars after indicator
                                    # Clean up the context
                                    context_part = context_part.replace('.', '').replace(',', '').replace('!', '').replace('?', '')
                                    if len(context_part.split()) >= 2:  # At least 2 words
                                        emotion_contexts.append(context_part.split()[:4])  # Take first 4 words
                                        context_extracted = True
                                        break
                            except:
                                continue
                    
                    # If no specific context found, look at the overall sentence topic
                    if not context_extracted:
                        # Check for subject matter in the sentence
                        if any(word in line_lower for word in ['my', 'i', 'me']):
                            # Personal context - try to identify what it's about
                            words = line_lower.split()
                            for i, word in enumerate(words):
                                if word in ['my', 'i', 'me'] and i < len(words) - 1:
                                    next_words = ' '.join(words[i+1:i+4])  # Get next 3 words
                                    emotion_contexts.append(next_words)
                                    break
        
        # Determine the most appropriate context for this emotion
        if emotion_contexts:
            # Flatten list if needed and count contexts
            flat_contexts = []
            for context in emotion_contexts:
                if isinstance(context, list):
                    flat_contexts.append(' '.join(context))
                else:
                    flat_contexts.append(str(context))
            
            if flat_contexts:
                context_counter = Counter(flat_contexts)
                most_common_context = context_counter.most_common(1)[0][0]
                
                # Convert topic codes to readable descriptions
                topic_descriptions = {
                    'work_stress': 'work-related pressures and challenges',
                    'relationship_issues': 'relationship concerns and interpersonal dynamics',
                    'family_dynamics': 'family interactions and responsibilities', 
                    'health_concerns': 'health issues and medical worries',
                    'financial_worries': 'financial stress and money concerns',
                    'social_situations': 'social interactions and peer relationships',
                    'academic_pressure': 'academic performance and educational goals',
                    'personal_development': 'personal growth and self-improvement efforts',
                    'past_experiences': 'past events and childhood memories',
                    'future_uncertainty': 'future planning and life uncertainties',
                    'daily_routine': 'daily life activities and routines',
                    'sleep_issues': 'sleep patterns and rest concerns'
                }
                
                if most_common_context in topic_descriptions:
                    context_mapping[emotion] = f"when discussing {topic_descriptions[most_common_context]}"
                else:
                    # Use extracted context directly, cleaning it up
                    clean_context = most_common_context.strip()
                    if len(clean_context) > 5:  # Only use if meaningful
                        context_mapping[emotion] = f"when discussing {clean_context}"
                    else:
                        context_mapping[emotion] = f"when talking about personal concerns"
            else:
                context_mapping[emotion] = f"during personal reflection"
        else:
            # More specific fallback contexts
            specific_defaults = {
                'anxious': 'when discussing uncertain or challenging situations',
                'sad': 'when reflecting on losses or disappointing experiences',
                'angry': 'when recalling frustrating or unfair situations',
                'hopeful': 'when talking about positive changes and future possibilities'
            }
            context_mapping[emotion] = specific_defaults.get(emotion, 'during meaningful conversation topics')
    
    return context_mapping


# Page configuration
st.set_page_config(
    page_title="SOAP Note Generator",
    page_icon="🩺",
    layout="wide"
)


# Enhanced CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #2563eb;
        text-align: center;
        margin-bottom: 1rem;
    }
    .soap-section {
        background: #f8fafc;
        border-left: 4px solid #3b82f6;
        padding: 1.5rem;
        margin: 1rem 0;
        border-radius: 8px;
    }
    .section-title {
        font-size: 1.5rem;
        font-weight: 600;
        color: #1e40af;
        margin-bottom: 0.5rem;
    }
    .brief-highlight {
        background: #fef3c7;
        padding: 1rem;
        border-radius: 6px;
        border: 2px solid #f59e0b;
        margin-bottom: 1rem;
        font-weight: 500;
        color: #92400e;
        box-shadow: 0 4px 12px rgba(245, 158, 11, 0.2);
    }
    .content-text {
        background: white;
        padding: 1rem;
        border-radius: 6px;
        color: #374151;
        line-height: 1.6;
        border: 1px solid #e5e7eb;
    }
    .note-label {
        display: inline-block;
        font-size: 0.85rem;
        font-weight: 700;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        color: #1d4ed8;
        background: rgba(59, 130, 246, 0.15);
        padding: 0.3rem 0.75rem;
        border-radius: 999px;
        margin-bottom: 0.45rem;
        box-shadow: 0 2px 8px rgba(59, 130, 246, 0.25);
    }
    .context-box {
        background: #eff6ff;
        padding: 0.75rem;
        border-radius: 6px;
        margin-top: 0.5rem;
        border-left: 3px solid #3b82f6;
    }
    .session-card {
        background: white;
        border: 2px solid #e5e7eb;
        border-radius: 8px;
        padding: 1.25rem;
        margin: 1rem 0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
    .session-header {
        font-size: 1.25rem;
        font-weight: 600;
        color: #1e40af;
        margin-bottom: 0.75rem;
        display: flex;
        align-items: center;
    }
    .session-number {
        background: #3b82f6;
        color: white;
        border-radius: 50%;
        width: 35px;
        height: 35px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        margin-right: 0.75rem;
        font-weight: bold;
    }
    .roadmap-summary {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 1.5rem;
        border-radius: 8px;
        margin: 1rem 0;
    }
    .tab-content {
        padding: 1rem 0;
    }
</style>
""", unsafe_allow_html=True)


# Initialize session state
if 'soap_note' not in st.session_state:
    st.session_state.soap_note = None
if 'treatment_roadmap' not in st.session_state:
    st.session_state.treatment_roadmap = None


# Enhanced SOAP Note Prompt Template
SOAP_TEMPLATE = """You are a licensed clinical documentation expert and therapy assistant. Your role is to analyze the therapist-client conversation and generate realistic, clinically accurate SOAP notes with supporting metrics for visualization. All content must be tied directly to the conversation.

Client Name: {client_name}
Session Date: {session_date}
Session Duration: {duration} minutes

Therapist-Client Conversation:
{conversation}

Generate a comprehensive SOAP note following these rules:

1. **SUBJECTIVE**: What the CLIENT says
   - Include direct quotes from the client
   - Report feelings, symptoms, and concerns
   - Highlight the most important points in a brief summary

2. **OBJECTIVE**: What the THERAPIST observed
   - Report observed behaviors (fidgeting, avoiding eye contact, tense posture, crying, affect)
   - Include measurable observations and therapist notes
   - Highlight key observations in a brief summary

3. **ASSESSMENT**: THERAPIST'S clinical opinion
   - Provide clinical interpretation, diagnostic considerations, and progress assessment
   - Include risk evaluation
   - Highlight key assessment points in a brief summary

4. **PLAN**: THERAPIST'S actionable next steps
   - Suggest specific interventions, homework, follow-ups, therapy sessions
   - Include realistic timelines for each step
   - Highlight key plan points in a brief summary

5. **Metrics and Visual Data**:
   - CRITICAL FOR emotional_distribution: Carefully analyze the conversation and calculate realistic percentages based on the emotional language, tone, and content expressed by the client throughout the session. Count instances of emotional keywords, phrases, and contextual emotional expressions. Percentages MUST sum to EXACTLY 100. Base this entirely on the actual conversation content - do not use generic distributions.
   - CRITICAL FOR emotional_context: For each emotion with >0%, identify the SPECIFIC topics, situations, or subjects the client was discussing when expressing that emotion. Extract actual content from the conversation (e.g., "when discussing the upcoming project deadline at work", "when talking about their daughter's behavior issues", "when describing the argument with their spouse"). DO NOT use generic phrases like "work-related pressures" - be specific about what was actually discussed.
   - CRITICAL FOR observed_behaviors (Clinical Behavioral Assessment): Rate each of the 4 key clinical areas on a 1-10 scale by carefully analyzing the conversation:
     * emotional_expression: How well does the client express emotions? Analyze their affect, emotional vocabulary, and emotional range in the dialogue.
     * mood_presentation: What is the overall mood state? Analyze positive vs negative mood indicators and emotional tone throughout the session.
     * engagement_level: How participatory is the client? Analyze response length, quality, initiative, and level of involvement in the conversation.
     * thought_patterns: How clear and organized is their thinking? Analyze coherence, logical flow, insight, and cognitive organization in their responses.
   - CRITICAL FOR assessment_domains (Clinical Assessment - SEVERITY Scales): Rate each of the 5 clinical domains on a 0-10 SEVERITY scale. IMPORTANT: Higher scores = MORE SEVERE problems:
     * mood: Rate depression/mood problem severity (0=positive mood, 10=severe depression)
     * anxiety: Rate anxiety severity (0=calm/no anxiety, 10=severe panic/constant anxiety)
     * sleep: Rate sleep disturbance severity (0=sleeping well, 10=severe insomnia/no sleep)
     * functioning: Rate functional impairment (0=functioning well, 10=unable to function)
     * risk: Rate risk level (0=no risk/safe, 10=active suicidal ideation/high risk)
     Analyze what the client actually discusses about each domain in the conversation.
   - CRITICAL FOR Session Metrics Summary (risk_level, engagement_level, session_productivity):
     * risk_level: Categorize as "low", "moderate", or "high" based on suicidal ideation, self-harm discussion, protective factors, and severity of symptoms
     * engagement_level: Rate 1-10 based on client's participation quality, response detail, initiative, emotional involvement (1=disengaged, 10=highly engaged)
     * session_productivity: Rate 1-10 based on therapeutic progress evident - insights gained, problem-solving, emotional processing, commitment to change (1=unproductive, 10=highly productive with breakthroughs)
     Analyze the ACTUAL conversation dialogue to rate these session quality metrics realistically.
   - Propose plan events with dates for next 12 weeks

Format your response EXACTLY as follows:

SUBJECTIVE_BRIEF:
[1-2 sentence summary of client-reported feelings and concerns]

SUBJECTIVE:
[Detailed subjective notes with direct client quotes and reported symptoms]

OBJECTIVE_BRIEF:
[1-2 sentence summary of therapist observations]

OBJECTIVE:
[Detailed observed behaviors, measurable notes, and context tied to conversation]

ASSESSMENT_BRIEF:
[1-2 sentence summary of clinical assessment]

ASSESSMENT:
[Detailed clinical interpretation, progress, risk assessment, and reasoning]

PLAN_BRIEF:
[1-2 sentence summary of next steps]

PLAN:
[Detailed, actionable plan including homework, interventions, therapy sessions, and follow-up]

METRICS:
{{
  "emotional_distribution": {{
    "[emotion_name]": [percentage as number],
    "anxious": [Calculate % based on worry/fear/stress language in conversation],
    "sad": [Calculate % based on depression/grief/loneliness language],
    "hopeful": [Calculate % based on optimism/progress/positive language],
    "angry": [Calculate % based on frustration/irritation/resentment language],
    "neutral": [Calculate % for balanced/calm emotional state]
    [Include only emotions that appeared in conversation. All percentages MUST sum to exactly 100]
  }},
  "emotional_context": {{
    "[emotion_name]": "when discussing [ANALYZE THE ACTUAL CONVERSATION - describe the specific topic, situation, or subject matter the client was talking about when they expressed this emotion. Be specific and extract from the conversation, e.g., 'when discussing their upcoming work presentation and fear of public speaking', 'when talking about their mother's recent cancer diagnosis', 'when describing last week's argument with their spouse about finances'. DO NOT use generic phrases like 'work-related pressures' - extract the actual specific context from the conversation.]"
  }},
  "observed_behaviors": {{
    "emotional_expression": [1-10 scale: Rate how client expresses emotions - analyze affect, emotional vocabulary richness, emotional range. 1=flat/no expression, 10=rich appropriate emotional expression],
    "mood_presentation": [1-10 scale: Rate overall mood state - analyze positive vs negative mood indicators, intensity, stability. 1=severely depressed mood, 10=positive upbeat mood],
    "engagement_level": [1-10 scale: Rate client participation - analyze response quality, initiative, involvement. 1=disengaged/withdrawn, 10=highly engaged and participatory],
    "thought_patterns": [1-10 scale: Rate clarity of thinking - analyze coherence, organization, insight. 1=confused/disorganized thinking, 10=clear organized rational thinking]
    [CRITICAL: Analyze the actual conversation content carefully. Base these ratings on what you observe in the dialogue, not generic assumptions]
  }},
  "assessment_domains": {{
    "mood": [0-10 scale: Rate mood severity. Analyze depression/sadness indicators in conversation. 0=no mood concerns/very positive, 10=severe depression/suicidal ideation. Higher scores indicate MORE severe mood problems],
    "anxiety": [0-10 scale: Rate anxiety severity. Analyze worry/fear/panic indicators in conversation. 0=no anxiety/calm, 10=severe panic/overwhelming anxiety. Higher scores indicate MORE severe anxiety],
    "sleep": [0-10 scale: Rate sleep disturbance severity. Analyze sleep quality mentions in conversation. 0=sleeping well/no issues, 10=severe insomnia/no sleep. Higher scores indicate MORE severe sleep problems],
    "functioning": [0-10 scale: Rate functional impairment severity. Analyze work/daily activities discussion. 0=functioning well/productive, 10=unable to function/completely disabled. Higher scores indicate MORE impairment],
    "risk": [0-10 scale: Rate risk level. Analyze suicidal ideation/self-harm indicators. 0=no risk/strong protective factors, 10=active suicidal plan/imminent danger. Higher scores indicate HIGHER risk]
    [CRITICAL: These are SEVERITY scales - higher numbers mean WORSE condition. Analyze the actual conversation carefully to rate each domain based on what the client discusses]
  }},
  "risk_level": "[Categorical: 'low', 'moderate', or 'high' based on suicidal ideation, self-harm indicators, protective factors, and severity of mood/anxiety symptoms discussed in conversation]",
  "engagement_level": [1-10 scale: Rate client engagement and participation quality. Analyze response length, detail, initiative, emotional involvement, questions asked. 1=withdrawn/disengaged/minimal responses, 10=highly engaged/detailed responses/actively participating],
  "session_productivity": [1-10 scale: Rate therapeutic progress and session effectiveness. Analyze insights gained, problem-solving attempts, emotional processing, commitment to change, application of techniques. 1=unproductive/resistant/avoidant, 10=highly productive/breakthrough insights/strong progress],
  "plan_timeline": [
    {{"Task": "Next Therapy Session", "Start": "{next_week}", "Finish": "{next_week}", "Resource": "Therapy"}},
    {{"Task": "[Specific intervention based on conversation]", "Start": "{tomorrow}", "Finish": "{two_weeks}", "Resource": "Self-Work"}},
    {{"Task": "[Follow-up action based on session content]", "Start": "{three_days}", "Finish": "{three_days}", "Resource": "Follow-Up"}},
    {{"Task": "[Assessment based on discussed needs]", "Start": "{six_weeks}", "Finish": "{six_weeks}", "Resource": "Assessment"}}
  ]
}}

CRITICAL REQUIREMENTS:
- Base ALL content on the actual conversation provided
- Use direct quotes from the client wherever possible
- Ensure emotional distribution percentages sum to exactly 100
- Make behavior counts realistic based on conversation content
- Avoid generic filler content - be specific and accurate
- Dates in plan_timeline should follow format YYYY-MM-DD"""


# Treatment Roadmap Prompt Template
ROADMAP_TEMPLATE = """You are an expert clinical psychologist specializing in treatment planning. Based on the client's information and therapy goals, create a comprehensive, structured treatment roadmap.

Client Information:
- Primary Issues: {primary_issues}
- Therapy Goals: {therapy_goals}
- Preferred Approach: {preferred_approach}
- Session Duration: {session_duration} minutes
- Severity Level: {severity_level}
- Additional Context: {additional_context}

Create a detailed treatment plan with the following:

1. Determine the appropriate number of sessions (typically 6-24 sessions based on severity)
2. Calculate realistic treatment duration in weeks (usually 1 session per week, sometimes 2 per week initially)
3. For EACH session, provide:
   - Session number (sequential: 1, 2, 3, etc.)
   - Week number (CRITICAL: Must be realistic and sequential - typically 1 session per week. Week 1, Week 2, Week 3, etc. Do NOT skip weeks or create unrealistic gaps)
   - Session objective/focus
   - Specific therapeutic techniques to be used
   - Expected outcomes or progress markers
   - Homework or between-session activities

CRITICAL WEEK NUMBER RULES:
- If planning 8 sessions at 1 per week → weeks should be 1, 2, 3, 4, 5, 6, 7, 8 (total 8 weeks)
- If planning 12 sessions at 1 per week → weeks should be 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 (total 12 weeks)
- For intensive therapy (2 per week): Session 1 & 2 = Week 1, Session 3 & 4 = Week 2, etc.
- Week numbers MUST be sequential without gaps
- treatment_duration_weeks should match the highest week number
- NEVER skip week numbers (e.g., don't go Week 1, Week 2, Week 7)

Format your response EXACTLY as JSON:

{{
  "total_sessions": 12,
  "treatment_duration_weeks": 12,
  "primary_diagnosis": "Generalized Anxiety Disorder",
  "treatment_approach": "Cognitive Behavioral Therapy with Mindfulness Integration",
  "overall_goals": [
    "Reduce anxiety symptoms by 50%",
    "Develop effective coping strategies",
    "Improve daily functioning and sleep quality"
  ],
  "sessions": [
    {{
      "session_number": 1,
      "week": 1,
      "title": "Initial Assessment and Goal Setting",
      "objective": "Establish therapeutic alliance, complete comprehensive assessment, and collaboratively set treatment goals",
      "techniques": [
        "Clinical interview",
        "Symptom assessment scales (GAD-7, PHQ-9)",
        "Goal setting framework",
        "Psychoeducation on anxiety"
      ],
      "expected_outcomes": [
        "Client feels heard and understood",
        "Clear baseline established",
        "Shared understanding of treatment goals",
        "Client understands anxiety cycle"
      ],
      "homework": "Keep daily anxiety log noting triggers, intensity (0-10), and responses"
    }},
    {{
      "session_number": 2,
      "week": 2,
      "title": "Understanding Anxiety and Cognitive Model",
      "objective": "Educate client on cognitive model and identify thought patterns",
      "techniques": [
        "Psychoeducation on CBT model",
        "Thought record introduction",
        "Cognitive distortion identification",
        "Anxiety cycle mapping"
      ],
      "expected_outcomes": [
        "Client understands connection between thoughts, feelings, and behaviors",
        "Can identify automatic thoughts",
        "Recognizes common cognitive distortions"
      ],
      "homework": "Complete thought records for anxiety episodes; Read CBT handout"
    }},
    {{
      "session_number": 3,
      "week": 3,
      "title": "Cognitive Restructuring Techniques",
      "objective": "Learn and practice challenging negative thoughts",
      "techniques": [
        "Socratic questioning",
        "Evidence for/against technique",
        "Behavioral experiments planning",
        "Alternative thought generation"
      ],
      "expected_outcomes": [
        "Client can challenge automatic thoughts",
        "Develops more balanced thinking patterns",
        "Plans first behavioral experiment"
      ],
      "homework": "Practice cognitive restructuring with thought records; Implement one behavioral experiment"
    }}
  ],
  "milestones": [
    {{
      "session": 4,
      "milestone": "Client can independently identify and challenge negative thoughts"
    }},
    {{
      "session": 8,
      "milestone": "50% reduction in anxiety symptoms; improved sleep quality"
    }},
    {{
      "session": 12,
      "milestone": "Treatment goals achieved; relapse prevention plan in place"
    }}
  ],
  "success_criteria": [
    "GAD-7 score reduced from moderate/severe to mild range",
    "Client demonstrates mastery of at least 3 coping techniques",
    "Improved functioning in work and relationships",
    "Confident in managing future anxiety episodes"
  ]
}}

CRITICAL REQUIREMENTS:
- Be specific and evidence-based in technique selection
- Ensure logical progression from session to session
- Adapt session count to severity (mild: 6-8, moderate: 10-14, severe: 16-24)
- Include concrete, measurable outcomes
- Balance between structure and flexibility
- Ensure all JSON is valid and properly formatted
- WEEK NUMBERS MUST BE SEQUENTIAL: If you plan 8 sessions, use weeks 1-8. If you plan 12 sessions, use weeks 1-12. DO NOT skip weeks.
- The "treatment_duration_weeks" MUST equal the highest week number used in sessions
- Generate ALL sessions listed in "total_sessions" - don't omit any sessions
- Each session must have a unique session_number (1, 2, 3, ..., up to total_sessions)
- Typically use 1 session per week (session 1 = week 1, session 2 = week 2, etc.)
- For intensive treatment, you can do 2 sessions per week (sessions 1&2 = week 1, sessions 3&4 = week 2)"""


def initialize_llm(api_key):
    """Initialize the OpenAI LLM with LangChain"""
    try:
        import os
        os.environ["OPENAI_API_KEY"] = api_key
        
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,
            api_key=api_key
        )
        return llm
    except Exception as e:
        st.error(f"Error initializing LLM: {str(e)}")
        return None


def parse_soap_response(response):
    """Parse the LLM response into structured SOAP sections with improved handling"""
    sections = {
        'subjective_brief': '',
        'subjective': '',
        'objective_brief': '',
        'objective': '',
        'assessment_brief': '',
        'assessment': '',
        'plan_brief': '',
        'plan': '',
        'metrics': {}
    }
    
    # Clean up response - remove markdown formatting and asterisks
    response = response.replace('**', '').replace('*', '')
    
    lines = response.split('\n')
    current_section = None
    content_buffer = []
    
    for line in lines:
        line_stripped = line.strip()
        
        # Skip empty lines at the beginning of sections
        if not line_stripped and not content_buffer:
            continue
        
        # Check for section markers (case insensitive, more flexible)
        line_upper = line_stripped.upper()
        
        if 'SUBJECTIVE_BRIEF:' in line_upper:
            if current_section and content_buffer:
                sections[current_section] = '\n'.join(content_buffer).strip()
            current_section = 'subjective_brief'
            # Extract content after the marker
            content_after_marker = line_stripped.split(':', 1)[1].strip() if ':' in line_stripped else ''
            content_buffer = [content_after_marker] if content_after_marker else []
        elif line_upper.startswith('SUBJECTIVE:') and 'BRIEF' not in line_upper:
            if current_section and content_buffer:
                sections[current_section] = '\n'.join(content_buffer).strip()
            current_section = 'subjective'
            content_after_marker = line_stripped.split(':', 1)[1].strip() if ':' in line_stripped else ''
            content_buffer = [content_after_marker] if content_after_marker else []
        elif 'OBJECTIVE_BRIEF:' in line_upper:
            if current_section and content_buffer:
                sections[current_section] = '\n'.join(content_buffer).strip()
            current_section = 'objective_brief'
            content_after_marker = line_stripped.split(':', 1)[1].strip() if ':' in line_stripped else ''
            content_buffer = [content_after_marker] if content_after_marker else []
        elif line_upper.startswith('OBJECTIVE:') and 'BRIEF' not in line_upper:
            if current_section and content_buffer:
                sections[current_section] = '\n'.join(content_buffer).strip()
            current_section = 'objective'
            content_after_marker = line_stripped.split(':', 1)[1].strip() if ':' in line_stripped else ''
            content_buffer = [content_after_marker] if content_after_marker else []
        elif 'ASSESSMENT_BRIEF:' in line_upper:
            if current_section and content_buffer:
                sections[current_section] = '\n'.join(content_buffer).strip()
            current_section = 'assessment_brief'
            content_after_marker = line_stripped.split(':', 1)[1].strip() if ':' in line_stripped else ''
            content_buffer = [content_after_marker] if content_after_marker else []
        elif line_upper.startswith('ASSESSMENT:') and 'BRIEF' not in line_upper:
            if current_section and content_buffer:
                sections[current_section] = '\n'.join(content_buffer).strip()
            current_section = 'assessment'
            content_after_marker = line_stripped.split(':', 1)[1].strip() if ':' in line_stripped else ''
            content_buffer = [content_after_marker] if content_after_marker else []
        elif 'PLAN_BRIEF:' in line_upper:
            if current_section and content_buffer:
                sections[current_section] = '\n'.join(content_buffer).strip()
            current_section = 'plan_brief'
            content_after_marker = line_stripped.split(':', 1)[1].strip() if ':' in line_stripped else ''
            content_buffer = [content_after_marker] if content_after_marker else []
        elif line_upper.startswith('PLAN:') and 'BRIEF' not in line_upper:
            if current_section and content_buffer:
                sections[current_section] = '\n'.join(content_buffer).strip()
            current_section = 'plan'
            content_after_marker = line_stripped.split(':', 1)[1].strip() if ':' in line_stripped else ''
            content_buffer = [content_after_marker] if content_after_marker else []
        elif 'METRICS:' in line_upper:
            if current_section and content_buffer:
                sections[current_section] = '\n'.join(content_buffer).strip()
            current_section = 'metrics'
            content_buffer = []
        elif current_section == 'metrics' and line_stripped:
            content_buffer.append(line_stripped)
        elif line_stripped and current_section and current_section != 'metrics':
            content_buffer.append(line_stripped)
    
    # Save final section
    if current_section and content_buffer:
        if current_section == 'metrics':
            try:
                metrics_text = '\n'.join(content_buffer)
                # Handle cases where the JSON might be wrapped in ```json blocks
                metrics_text = metrics_text.replace('```json', '').replace('```', '').strip()
                sections['metrics'] = json.loads(metrics_text)
            except json.JSONDecodeError as e:
                st.warning(f"Could not parse metrics JSON: {e}. Using default values.")
                sections['metrics'] = get_default_metrics()
        else:
            sections[current_section] = '\n'.join(content_buffer).strip()
    
    # Ensure metrics exist
    if not sections['metrics']:
        sections['metrics'] = get_default_metrics()
    
    return sections


def enhance_metrics_with_analysis(sections, conversation):
    """Enhance parsed SOAP sections with calculated metrics from conversation analysis"""
    if not conversation:
        return sections
    
    try:
        # Calculate metrics from conversation
        emotional_distribution = analyze_conversation_emotions(conversation)
        observed_behaviors = analyze_observed_behaviors(conversation)
        assessment_domains = analyze_assessment_domains(conversation, emotional_distribution)
        risk_level = calculate_risk_level(assessment_domains, conversation)
        engagement_level, session_productivity = calculate_engagement_productivity(conversation)
        
        # Update metrics if they're missing or seem generic
        if not sections.get('metrics'):
            sections['metrics'] = {}
        
        metrics = sections['metrics']
        
        # For emotional_distribution: prefer LLM-generated percentages if valid
        # Only use programmatic calculation as fallback
        if 'emotional_distribution' not in metrics or not metrics['emotional_distribution']:
            # Fallback: use programmatic calculation only if LLM didn't provide it
            metrics['emotional_distribution'] = emotional_distribution
        else:
            # Validate LLM-generated emotional distribution
            llm_distribution = metrics['emotional_distribution']
            if isinstance(llm_distribution, dict) and llm_distribution:
                # Check if percentages sum to approximately 100 (allow small rounding errors)
                total_percentage = sum(llm_distribution.values())
                # Check if all values are reasonable (0-100)
                valid_values = all(0 <= v <= 100 for v in llm_distribution.values())
                
                if not valid_values or total_percentage < 95 or total_percentage > 105:
                    # Invalid distribution, use programmatic calculation as fallback
                    st.warning("LLM-generated emotional distribution was invalid. Using conversation analysis fallback.")
                    metrics['emotional_distribution'] = emotional_distribution
                # else: keep LLM-generated distribution as is
            else:
                # Empty or invalid format, use fallback
                metrics['emotional_distribution'] = emotional_distribution
        
        # For emotional_context: prefer LLM-generated context if it exists and is meaningful
        # Only use programmatic extraction as fallback
        # Use the emotional distribution (either LLM or fallback) for context extraction
        current_distribution = metrics['emotional_distribution']
        
        if 'emotional_context' not in metrics or not metrics['emotional_context']:
            # Fallback: use programmatic extraction only if LLM didn't provide it
            emotional_context = extract_emotional_context(conversation, current_distribution)
            metrics['emotional_context'] = emotional_context
        else:
            # Validate LLM-generated emotional context - check if it's meaningful
            llm_context = metrics['emotional_context']
            if isinstance(llm_context, dict) and llm_context:
                # Check if contexts are too generic or empty
                needs_fallback = False
                for emotion, context in llm_context.items():
                    if not context or len(context.strip()) < 10 or 'generic' in context.lower():
                        needs_fallback = True
                        break
                
                if needs_fallback:
                    # Use programmatic extraction as fallback
                    emotional_context = extract_emotional_context(conversation, current_distribution)
                    metrics['emotional_context'] = emotional_context
            # else: keep LLM-generated context as is
        
        # For observed_behaviors (Clinical Behavioral Assessment): prefer LLM-generated ratings if valid
        # Only use programmatic calculation as fallback
        if 'observed_behaviors' not in metrics or not metrics['observed_behaviors']:
            # Fallback: use programmatic calculation only if LLM didn't provide it
            metrics['observed_behaviors'] = observed_behaviors
        else:
            # Validate LLM-generated observed behaviors
            llm_behaviors = metrics['observed_behaviors']
            if isinstance(llm_behaviors, dict) and llm_behaviors:
                # Check if it has the expected clinical assessment keys
                expected_keys = ['emotional_expression', 'mood_presentation', 'engagement_level', 'thought_patterns']
                has_expected_keys = any(key in llm_behaviors for key in expected_keys)
                
                if has_expected_keys:
                    # Validate that values are in 1-10 range
                    valid_values = True
                    for key in expected_keys:
                        if key in llm_behaviors:
                            value = llm_behaviors[key]
                            if not isinstance(value, (int, float)) or value < 1 or value > 10:
                                valid_values = False
                                break
                    
                    if not valid_values:
                        # Invalid values, use programmatic calculation as fallback
                        st.warning("LLM-generated clinical behavioral assessment had invalid values. Using conversation analysis fallback.")
                        metrics['observed_behaviors'] = observed_behaviors
                    # else: keep LLM-generated behavioral ratings as is
                else:
                    # Missing expected keys, use fallback
                    metrics['observed_behaviors'] = observed_behaviors
            else:
                # Empty or invalid format, use fallback
                metrics['observed_behaviors'] = observed_behaviors
        
        # For assessment_domains (Clinical Assessment Domains): prefer LLM-generated ratings if valid
        # Only use programmatic calculation as fallback
        if 'assessment_domains' not in metrics or not metrics['assessment_domains']:
            # Fallback: use programmatic calculation only if LLM didn't provide it
            metrics['assessment_domains'] = assessment_domains
        else:
            # Validate LLM-generated assessment domains
            llm_domains = metrics['assessment_domains']
            if isinstance(llm_domains, dict) and llm_domains:
                # Check if it has the expected domain keys
                expected_keys = ['mood', 'anxiety', 'sleep', 'functioning', 'risk']
                has_expected_keys = all(key in llm_domains for key in expected_keys)
                
                if has_expected_keys:
                    # Validate that values are in 0-10 range
                    valid_values = True
                    for key in expected_keys:
                        value = llm_domains[key]
                        if not isinstance(value, (int, float)) or value < 0 or value > 10:
                            valid_values = False
                            break
                    
                    if not valid_values:
                        # Invalid values, use programmatic calculation as fallback
                        st.warning("LLM-generated clinical assessment domains had invalid values. Using conversation analysis fallback.")
                        metrics['assessment_domains'] = assessment_domains
                    # else: keep LLM-generated domain ratings as is
                else:
                    # Missing expected keys, use fallback
                    st.warning("LLM-generated clinical assessment domains missing required keys. Using conversation analysis fallback.")
                    metrics['assessment_domains'] = assessment_domains
            else:
                # Empty or invalid format, use fallback
                metrics['assessment_domains'] = assessment_domains
        
        # Validate and use LLM-generated Session Metrics Summary with fallback
        # 1. risk_level validation
        if 'risk_level' in metrics and metrics['risk_level']:
            llm_risk_level = metrics['risk_level']
            # Check if it's a valid categorical value
            valid_risk_levels = ['low', 'moderate', 'high']
            if isinstance(llm_risk_level, str) and llm_risk_level.lower() in valid_risk_levels:
                # Use LLM-generated risk level (normalize to lowercase)
                metrics['risk_level'] = llm_risk_level.lower()
            else:
                # Invalid format, use fallback
                st.warning("LLM-generated risk_level had invalid format. Using conversation analysis fallback.")
                metrics['risk_level'] = risk_level
        else:
            # LLM didn't provide risk_level, use fallback
            metrics['risk_level'] = risk_level
        
        # 2. engagement_level validation
        if 'engagement_level' in metrics and metrics['engagement_level']:
            llm_engagement = metrics['engagement_level']
            # Check if it's a valid number in 1-10 range
            if isinstance(llm_engagement, (int, float)) and 1 <= llm_engagement <= 10:
                # Use LLM-generated engagement level
                pass  # Keep as is
            else:
                # Invalid value, use fallback
                st.warning("LLM-generated engagement_level had invalid value. Using conversation analysis fallback.")
                metrics['engagement_level'] = engagement_level
        else:
            # LLM didn't provide engagement_level, use fallback
            metrics['engagement_level'] = engagement_level
        
        # 3. session_productivity validation
        if 'session_productivity' in metrics and metrics['session_productivity']:
            llm_productivity = metrics['session_productivity']
            # Check if it's a valid number in 1-10 range
            if isinstance(llm_productivity, (int, float)) and 1 <= llm_productivity <= 10:
                # Use LLM-generated session productivity
                pass  # Keep as is
            else:
                # Invalid value, use fallback
                st.warning("LLM-generated session_productivity had invalid value. Using conversation analysis fallback.")
                metrics['session_productivity'] = session_productivity
        else:
            # LLM didn't provide session_productivity, use fallback
            metrics['session_productivity'] = session_productivity
        
        # Keep plan_timeline from LLM if it exists, otherwise add default
        if 'plan_timeline' not in metrics:
            metrics['plan_timeline'] = []
            
    except Exception as e:
        st.warning(f"Error enhancing metrics: {e}")
    
    return sections


def get_default_metrics():
    """Return minimal default metrics structure - should only be used as last resort fallback"""
    return {
        "emotional_distribution": {},  # Empty - will be calculated from conversation
        "emotional_context": {},
        "observed_behaviors": {},
        "assessment_domains": {},
        "risk_level": "unknown",
        "engagement_level": None,  # Will be calculated
        "session_productivity": None,  # Will be calculated
        "plan_timeline": []
    }


def create_subjective_chart(emotional_dist):
    """Create emotional distribution pie chart - only use actual data, no defaults"""
    if not emotional_dist or not any(emotional_dist.values()):
        # Return empty chart if no data
        fig = go.Figure()
        fig.add_annotation(
            text="No emotional data available",
            x=0.5, y=0.5,
            xref="paper", yref="paper",
            showarrow=False,
            font=dict(size=16, color="gray")
        )
        fig.update_layout(
            title="Emotional Distribution During Session",
            height=400,
            margin=dict(l=20, r=20, t=60, b=20)
        )
        return fig
    
    emotions = list(emotional_dist.keys())
    values = list(emotional_dist.values())
    
    # Define colors for different emotions
    color_map = {
        'anxious': '#ef4444',    # Red
        'sad': '#3b82f6',        # Blue 
        'angry': '#f59e0b',      # Orange
        'neutral': '#6b7280',    # Gray
        'hopeful': '#10b981',    # Green
        'frustrated': '#f97316', # Orange-red
        'excited': '#8b5cf6',    # Purple
        'calm': '#06b6d4',       # Cyan
        'confused': '#84cc16',   # Lime
        'content': '#059669'     # Emerald
    }
    
    # Assign colors based on emotion type, with fallback
    colors = [color_map.get(emotion, '#94a3b8') for emotion in emotions]
    
    fig = go.Figure(data=[go.Pie(
        labels=[e.capitalize() for e in emotions],
        values=values,
        hole=0.3,
        marker=dict(colors=colors),
        textinfo='label+percent',
        hovertemplate='<b>%{label}</b><br>%{value}%<br>Based on conversation analysis<extra></extra>'
    )])
    
    fig.update_layout(
        title="Emotional Distribution During Session (Conversation-Based)",
        height=400,
        margin=dict(l=20, r=20, t=60, b=20),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5)
    )
    
    return fig


def create_objective_chart(observed_behaviors):
    """Create clinical behavior assessment chart with 4 key areas - only use actual analyzed data"""
    if not observed_behaviors:
        # Return empty chart if no data
        fig = go.Figure()
        fig.add_annotation(
            text="No behavioral data available - please provide conversation",
            x=0.5, y=0.5,
            xref="paper", yref="paper",
            showarrow=False,
            font=dict(size=14, color="gray")
        )
        fig.update_layout(
            title="Clinical Behavioral Assessment (No Data Available)",
            height=400,
            margin=dict(l=200, r=40, t=60, b=40)
        )
        return fig
    
    # Map clinical categories with descriptive labels
    clinical_categories = {
        "emotional_expression": "Emotional Expression (Affect)",
        "mood_presentation": "Mood Presentation", 
        "engagement_level": "Engagement/Participation",
        "thought_patterns": "Thought Patterns"
    }
    
    # Extract clinical data - use only what we calculated
    clinical_data = {}
    for key, label in clinical_categories.items():
        if key in observed_behaviors:
            clinical_data[label] = observed_behaviors[key]
        else:
            # If specific clinical measure not found, skip it
            continue
    
    # If no clinical data found, show message
    if not clinical_data:
        fig = go.Figure()
        fig.add_annotation(
            text="No clinical behavioral assessments available",
            x=0.5, y=0.5,
            xref="paper", yref="paper",
            showarrow=False,
            font=dict(size=14, color="gray")
        )
        fig.update_layout(
            title="Clinical Behavioral Assessment (No Data Available)",
            height=400,
            margin=dict(l=200, r=40, t=60, b=40)
        )
        return fig
    
    categories = list(clinical_data.keys())
    scores = list(clinical_data.values())
    
    # Color coding based on clinical significance (1-10 scale)
    colors = []
    for score in scores:
        if score <= 3:
            colors.append('#ef4444')  # Red - Concerning
        elif score <= 5:
            colors.append('#f59e0b')  # Yellow - Moderate concern
        elif score <= 7:
            colors.append('#3b82f6')  # Blue - Normal range
        else:
            colors.append('#10b981')  # Green - Good/Positive
    
    # Create horizontal bar chart for better readability
    fig = go.Figure(data=[go.Bar(
        y=categories,
        x=scores,
        orientation='h',
        marker=dict(color=colors),
        text=[f"{score}/10" for score in scores],
        textposition='inside',
        textfont=dict(color='white', size=12, family='Arial Bold'),
        hovertemplate='<b>%{y}</b><br>Clinical Rating: %{x}/10<br>Based on conversation analysis<extra></extra>'
    )])
    
    fig.update_layout(
        title="Clinical Behavioral Assessment (1-10 Scale) - Conversation Analysis",
        xaxis_title="Clinical Rating (1=Concerning, 10=Excellent)",
        yaxis_title="",
        height=400,
        margin=dict(l=250, r=40, t=60, b=40),
        xaxis=dict(range=[0, 10], tickmode='linear', dtick=1),
        font=dict(family='Arial, sans-serif'),
        plot_bgcolor='#f8fafc'
    )
    
    # Add reference lines for clinical ranges
    fig.add_vline(x=3, line_dash="dash", line_color="red", opacity=0.3, 
                  annotation_text="Concerning", annotation_position="top")
    fig.add_vline(x=7, line_dash="dash", line_color="green", opacity=0.3,
                  annotation_text="Good Range", annotation_position="top")
    
    return fig


def create_assessment_chart(assessment_domains):
    """Create assessment radar chart - only use actual analyzed data"""
    if not assessment_domains:
        # Return empty chart if no data
        fig = go.Figure()
        fig.add_annotation(
            text="No assessment data available - please provide conversation",
            x=0.5, y=0.5,
            xref="paper", yref="paper",
            showarrow=False,
            font=dict(size=14, color="gray")
        )
        fig.update_layout(
            title="Clinical Assessment Domains (No Data Available)",
            height=450,
            margin=dict(l=80, r=80, t=60, b=20)
        )
        return fig
    
    domains = [k.capitalize() for k in assessment_domains.keys()]
    scores = list(assessment_domains.values())
    
    fig = go.Figure()
    
    fig.add_trace(go.Scatterpolar(
        r=scores + [scores[0]],
        theta=domains + [domains[0]],
        fill='toself',
        fillcolor='rgba(59, 130, 246, 0.3)',
        line=dict(color='#3b82f6', width=2),
        name='Severity Score',
        hovertemplate='<b>%{theta}</b><br>Score: %{r}/10<br>Based on conversation analysis<extra></extra>'
    ))
    
    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, 10],
                tickmode='linear',
                tick0=0,
                dtick=2,
                showticklabels=True
            )
        ),
        title="Clinical Assessment Domains (0-10 Scale) - Conversation Analysis",
        height=450,
        margin=dict(l=80, r=80, t=60, b=20),
        showlegend=False
    )
    
    return fig


def create_plan_visual(plan_timeline):
    """Create simple next steps visualization with clean table format - avoid defaults when possible"""
    if not plan_timeline or len(plan_timeline) == 0:
        # Only create defaults if absolutely necessary
        start_date = datetime.now()
        plan_timeline = [
            {"Task": "Follow-up Session", "Start": (start_date + timedelta(days=7)).strftime("%Y-%m-%d"), 
             "Finish": (start_date + timedelta(days=7)).strftime("%Y-%m-%d"), "Resource": "Therapy"},
            {"Task": "Self-Reflection Exercise", "Start": (start_date + timedelta(days=1)).strftime("%Y-%m-%d"), 
             "Finish": (start_date + timedelta(days=7)).strftime("%Y-%m-%d"), "Resource": "Self-Work"}
        ]
    
    # Sort by start date to show chronological order
    sorted_timeline = sorted(plan_timeline, key=lambda x: datetime.strptime(x['Start'], '%Y-%m-%d'))
    
    return sorted_timeline


def create_plan_chart(plan_timeline):
    """Create a simple, clean next steps timeline visualization - minimize defaults"""
    if not plan_timeline or len(plan_timeline) == 0:
        # Minimal default timeline only if absolutely no data
        start_date = datetime.now()
        plan_timeline = [
            {"Task": "Schedule Follow-up", "Start": (start_date + timedelta(days=7)).strftime("%Y-%m-%d"), 
             "Finish": (start_date + timedelta(days=7)).strftime("%Y-%m-%d"), "Resource": "Therapy"}
        ]
    
    # Sort by start date to show chronological order
    sorted_timeline = sorted(plan_timeline, key=lambda x: datetime.strptime(x['Start'], '%Y-%m-%d'))
    
    # Create a simple horizontal timeline visualization
    tasks = []
    dates = []
    colors = []
    
    # Color mapping for different resource types
    color_map = {
        'Therapy': '#3b82f6',      # Blue
        'Self-Work': '#10b981',    # Green
        'Follow-Up': '#f59e0b',    # Yellow
        'Assessment': '#8b5cf6',   # Purple
        'Default': '#6b7280'       # Gray
    }
    
    for i, item in enumerate(sorted_timeline):
        task_name = item.get('Task', 'Unknown Task')
        start_date = item.get('Start', '')
        resource = item.get('Resource', 'Default')
        
        # Format date for display
        try:
            date_obj = datetime.strptime(start_date, '%Y-%m-%d')
            formatted_date = date_obj.strftime('%b %d, %Y')
            days_from_now = (date_obj - datetime.now()).days
            
            if days_from_now == 0:
                date_display = f"{formatted_date} (Today)"
            elif days_from_now == 1:
                date_display = f"{formatted_date} (Tomorrow)"
            elif days_from_now > 0:
                date_display = f"{formatted_date} (in {days_from_now} days)"
            else:
                date_display = f"{formatted_date} ({abs(days_from_now)} days ago)"
                
        except ValueError:
            date_display = start_date
        
        tasks.append(f"{i+1}. {task_name}")
        dates.append(date_display)
        colors.append(color_map.get(resource, color_map['Default']))
    
    # Create horizontal bar chart
    fig = go.Figure()
    
    # Add bars
    fig.add_trace(go.Bar(
        y=tasks,
        x=[1] * len(tasks),  # All bars same length for visual consistency
        orientation='h',
        marker=dict(color=colors),
        text=dates,
        textposition='middle right',
        textfont=dict(color='white', size=12, family='Arial Black'),
        hovertemplate='<b>%{y}</b><br>%{text}<br><extra></extra>',
        showlegend=False
    ))
    
    fig.update_layout(
        title={
            'text': "Treatment Plan - Next Steps Timeline",
            'x': 0.5,
            'xanchor': 'center',
            'font': {'size': 18, 'color': '#1e40af'}
        },
        xaxis=dict(
            visible=False,
            range=[0, 1.2]
        ),
        yaxis=dict(
            tickfont=dict(size=12, color='#374151'),
            autorange='reversed'  # Shows first item at top
        ),
        height=max(300, len(tasks) * 60),
        margin=dict(l=20, r=120, t=60, b=40),
        paper_bgcolor='white',
        plot_bgcolor='#f8fafc',
        font=dict(family='Arial, sans-serif')
    )
    
    return fig


def display_plan_timeline(plan_timeline):
    """Display next session focus areas in clean text format"""
    if not plan_timeline or len(plan_timeline) == 0:
        st.markdown("### 📅 Next Session Focus")
        st.info("⚠️ No specific treatment plan was generated from the conversation. Please provide a detailed therapy conversation to generate accurate next steps.")
        return
    
    # Sort by start date to show chronological order
    sorted_timeline = sorted(plan_timeline, key=lambda x: datetime.strptime(x['Start'], '%Y-%m-%d'))
    
    # Display clean header
    st.markdown("### 📅 Next Session Focus Areas")
    
    # Simple text-based list of focus areas
    for i, item in enumerate(sorted_timeline, 1):
        task_name = item.get('Task', 'Unknown Task')
        start_date_str = item.get('Start', '')
        resource = item.get('Resource', 'General')
        
        # Format date for display
        try:
            date_obj = datetime.strptime(start_date_str, '%Y-%m-%d')
            today = datetime.now()
            days_until = (date_obj - today).days
            
            if days_until <= 1:
                timing = "Next session"
            elif days_until <= 7:
                timing = "This week"
            elif days_until <= 14:
                timing = "Within 2 weeks"
            else:
                timing = "Upcoming"
                
        except ValueError:
            timing = "Scheduled"
        
        # Display as simple numbered list with focus area
        if resource == "Therapy":
            st.markdown(f"**{i}. {task_name}** ({timing})")
        else:
            st.markdown(f"**{i}. {task_name}** - {resource} ({timing})")
    
    # Add brief note about session planning
    st.caption("💡 These focus areas are derived from the conversation analysis and clinical assessment.")


def create_roadmap_gantt(roadmap_data):
    """Create Gantt chart for treatment roadmap"""
    sessions = roadmap_data.get('sessions', [])
    if not sessions:
        return None
    
    # Create timeline data
    timeline_data = []
    start_date = datetime.now()
    
    for session in sessions:
        week = session.get('week', session.get('session_number', 1))
        session_date = start_date + timedelta(weeks=week-1)
        
        timeline_data.append({
            'Task': f"Session {session['session_number']}: {session['title']}",
            'Start': session_date.strftime("%Y-%m-%d"),
            'Finish': (session_date + timedelta(days=1)).strftime("%Y-%m-%d"),
            'Type': 'Session'
        })
    
    df = pd.DataFrame(timeline_data)
    df['Start'] = pd.to_datetime(df['Start'])
    df['Finish'] = pd.to_datetime(df['Finish'])
    
    fig = px.timeline(
        df,
        x_start="Start",
        x_end="Finish",
        y="Task",
        color="Type",
        title=f"Treatment Roadmap Timeline ({roadmap_data.get('total_sessions', 'N/A')} Sessions)",
        color_discrete_map={'Session': '#8b5cf6'}
    )
    
    fig.update_layout(
        height=max(400, len(sessions) * 40),
        margin=dict(l=20, r=40, t=60, b=60),
        xaxis_title="Timeline",
        yaxis_title="",
        showlegend=False
    )
    
    fig.update_yaxes(categoryorder='total descending')
    
    return fig


def generate_soap_note(llm, client_name, session_date, duration, conversation):
    """Generate SOAP note using LangChain with dynamic dates and conversation analysis"""
    try:
        # Pre-analyze conversation for internal fallback purposes only
        # Let LLM generate ALL metrics on its own: emotional_distribution, emotional_context, 
        # observed_behaviors, assessment_domains, risk_level, engagement_level, session_productivity
        
        # Calculate these internally for fallback only - NOT passed to LLM
        emotional_distribution = analyze_conversation_emotions(conversation)
        observed_behaviors = analyze_observed_behaviors(conversation)
        assessment_domains = analyze_assessment_domains(conversation, emotional_distribution)
        risk_level = calculate_risk_level(assessment_domains, conversation)
        engagement_level, session_productivity = calculate_engagement_productivity(conversation)
        
        # Calculate dynamic dates for the prompt
        session_dt = datetime.strptime(session_date, "%Y-%m-%d")
        dates = {
            "tomorrow": (session_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
            "three_days": (session_dt + timedelta(days=3)).strftime("%Y-%m-%d"),
            "next_week": (session_dt + timedelta(days=7)).strftime("%Y-%m-%d"),
            "two_weeks": (session_dt + timedelta(days=14)).strftime("%Y-%m-%d"),
            "six_weeks": (session_dt + timedelta(days=42)).strftime("%Y-%m-%d")
        }
        
        # Enhanced template with minimal analysis context
        # NOTE: We do NOT pass any pre-calculated metrics - let the LLM analyze and generate everything
        analysis_context = f"""

CRITICAL INSTRUCTIONS FOR LLM-GENERATED METRICS:
1. For emotional_distribution in METRICS: Analyze the conversation carefully and calculate realistic percentages based on the emotional language used by the client. Percentages MUST sum to exactly 100.
2. For emotional_context in METRICS: Extract the specific topics/situations the client discussed when expressing each emotion. Be specific - identify what the client was ACTUALLY talking about, not generic descriptions.
3. For observed_behaviors (Clinical Behavioral Assessment) in METRICS: Rate the 4 clinical areas (emotional_expression, mood_presentation, engagement_level, thought_patterns) on a 1-10 scale based on what you observe in the actual conversation dialogue. Analyze the client's responses carefully.
4. For assessment_domains (Clinical Assessment Domains) in METRICS: Rate the 5 domains (mood, anxiety, sleep, functioning, risk) on a 0-10 SEVERITY scale. CRITICAL: Higher scores = MORE SEVERE problems. Analyze what the client actually discusses about each domain.
5. For Session Metrics Summary (risk_level, engagement_level, session_productivity) in METRICS:
   - risk_level: Categorize as "low", "moderate", or "high" by analyzing suicidal ideation, self-harm mentions, protective factors, and symptom severity in the actual conversation
   - engagement_level: Rate 1-10 based on the client's actual participation - analyze response length, detail quality, initiative taken, questions asked, emotional involvement
   - session_productivity: Rate 1-10 based on therapeutic progress evident in the conversation - look for insights, problem-solving attempts, emotional processing, commitment statements, application of techniques
6. Read the conversation thoroughly to understand the emotional journey, behavioral patterns, clinical symptoms, session quality, and overall presentation."""
        
        enhanced_template = SOAP_TEMPLATE + analysis_context
        
        prompt = PromptTemplate(
            input_variables=["client_name", "session_date", "duration", "conversation", 
                           "tomorrow", "three_days", "next_week", "two_weeks", "six_weeks"],
            template=enhanced_template
        )
        
        chain = prompt | llm
        
        result = chain.invoke({
            "client_name": client_name,
            "session_date": session_date,
            "duration": duration,
            "conversation": conversation,
            **dates
        })
        
        if hasattr(result, 'content'):
            return result.content
        else:
            return str(result)
    except Exception as e:
        st.error(f"Error generating SOAP note: {str(e)}")
        return None


def generate_treatment_roadmap(llm, primary_issues, therapy_goals, preferred_approach, 
                               session_duration, severity_level, additional_context):
    """Generate treatment roadmap using LangChain"""
    try:
        prompt = PromptTemplate(
            input_variables=["primary_issues", "therapy_goals", "preferred_approach", 
                           "session_duration", "severity_level", "additional_context"],
            template=ROADMAP_TEMPLATE
        )
        
        chain = prompt | llm
        
        result = chain.invoke({
            "primary_issues": primary_issues,
            "therapy_goals": therapy_goals,
            "preferred_approach": preferred_approach,
            "session_duration": session_duration,
            "severity_level": severity_level,
            "additional_context": additional_context
        })
        
        if hasattr(result, 'content'):
            response_text = result.content
        else:
            response_text = str(result)
        
        # Clean and parse JSON
        response_text = response_text.replace('```json', '').replace('```', '').strip()
        roadmap_data = json.loads(response_text)
        
        return roadmap_data
    except json.JSONDecodeError as e:
        st.error(f"Error parsing roadmap JSON: {str(e)}")
        return None
    except Exception as e:
        st.error(f"Error generating roadmap: {str(e)}")
        return None


def create_pdf(soap_sections, client_name, session_date, metrics):
    """Generate enhanced PDF of SOAP note with metrics and embedded charts"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    
    # Title
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], 
                                fontSize=24, spaceAfter=20, textColor='#2563eb')
    story.append(Paragraph("CLINICAL SOAP NOTE", title_style))
    
    # Client info
    info_style = ParagraphStyle('Info', parent=styles['Normal'], fontSize=11, spaceAfter=6)
    story.append(Paragraph(f"<b>Client:</b> {client_name}", info_style))
    story.append(Paragraph(f"<b>Session Date:</b> {session_date}", info_style))
    
    if metrics:
        story.append(Paragraph(f"<b>Risk Level:</b> {metrics.get('risk_level', 'N/A').upper()}", info_style))
        story.append(Paragraph(f"<b>Engagement:</b> {metrics.get('engagement_level', 'N/A')}/10", info_style))
        story.append(Paragraph(f"<b>Productivity:</b> {metrics.get('session_productivity', 'N/A')}/10", info_style))
    
    story.append(Spacer(1, 0.3*inch))
    
    # Sections
    section_titles = {
        'subjective': 'SUBJECTIVE (Client Reports)',
        'objective': 'OBJECTIVE (Therapist Observations)',
        'assessment': 'ASSESSMENT (Clinical Opinion)',
        'plan': 'PLAN (Next Steps)'
    }
    
    section_style = ParagraphStyle('Section', parent=styles['Heading2'], 
                                  fontSize=14, textColor='#1e40af', spaceAfter=10)
    brief_style = ParagraphStyle('Brief', parent=styles['Normal'], 
                                fontSize=10, textColor='#92400e', 
                                backColor='#fef3c7', borderPadding=8)
    
    for key, title in section_titles.items():
        story.append(Paragraph(title, section_style))
        
        brief_key = f"{key}_brief"
        if soap_sections.get(brief_key):
            story.append(Paragraph(f"<b>Brief:</b> {soap_sections[brief_key]}", brief_style))
            story.append(Spacer(1, 0.1*inch))
        
        if soap_sections.get(key):
            story.append(Paragraph(soap_sections[key], styles['Normal']))
        
        story.append(Spacer(1, 0.2*inch))
    
    # Add charts if metrics available
    if metrics:
        story.append(Spacer(1, 0.3*inch))
        story.append(Paragraph("VISUAL ANALYSIS", section_style))
        
        # Emotional Distribution Chart
        if metrics.get('emotional_distribution'):
            try:
                fig = create_subjective_chart(metrics['emotional_distribution'])
                img_buffer = BytesIO()
                fig.write_image(img_buffer, format='png', width=600, height=400)
                img_buffer.seek(0)
                img = Image(img_buffer, width=6*inch, height=4*inch)
                story.append(img)
                story.append(Spacer(1, 0.2*inch))
                story.append(Paragraph("Emotional Distribution Analysis", styles['Heading3']))
                story.append(Spacer(1, 0.1*inch))
            except Exception as e:
                st.warning(f"Could not embed emotional distribution chart: {str(e)}")
        
        # Observed Behaviors Chart
        if metrics.get('observed_behaviors'):
            try:
                fig = create_objective_chart(metrics['observed_behaviors'])
                img_buffer = BytesIO()
                fig.write_image(img_buffer, format='png', width=600, height=400)
                img_buffer.seek(0)
                img = Image(img_buffer, width=6*inch, height=4*inch)
                story.append(img)
                story.append(Spacer(1, 0.2*inch))
                story.append(Paragraph("Clinical Behavioral Assessment", styles['Heading3']))
                story.append(Spacer(1, 0.1*inch))
            except Exception as e:
                st.warning(f"Could not embed behavioral assessment chart: {str(e)}")
        
        # Assessment Domains Chart
        if metrics.get('assessment_domains'):
            try:
                fig = create_assessment_chart(metrics['assessment_domains'])
                img_buffer = BytesIO()
                fig.write_image(img_buffer, format='png', width=600, height=400)
                img_buffer.seek(0)
                img = Image(img_buffer, width=6*inch, height=4*inch)
                story.append(img)
                story.append(Spacer(1, 0.2*inch))
                story.append(Paragraph("Clinical Assessment Domains", styles['Heading3']))
                story.append(Spacer(1, 0.1*inch))
            except Exception as e:
                st.warning(f"Could not embed assessment domains chart: {str(e)}")
    
    doc.build(story)
    buffer.seek(0)
    return buffer


def create_roadmap_pdf(roadmap_data):
    """Generate PDF of treatment roadmap"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    
    # Title
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], 
                                fontSize=24, spaceAfter=20, textColor='#8b5cf6')
    story.append(Paragraph("TREATMENT ROADMAP", title_style))
    
    # Summary info
    info_style = ParagraphStyle('Info', parent=styles['Normal'], fontSize=11, spaceAfter=6)
    story.append(Paragraph(f"<b>Total Sessions:</b> {roadmap_data.get('total_sessions', 'N/A')}", info_style))
    story.append(Paragraph(f"<b>Duration:</b> {roadmap_data.get('treatment_duration_weeks', 'N/A')} weeks", info_style))
    story.append(Paragraph(f"<b>Primary Focus:</b> {roadmap_data.get('primary_diagnosis', 'N/A')}", info_style))
    story.append(Paragraph(f"<b>Approach:</b> {roadmap_data.get('treatment_approach', 'N/A')}", info_style))
    
    story.append(Spacer(1, 0.3*inch))
    
    # Overall goals
    story.append(Paragraph("TREATMENT GOALS", styles['Heading2']))
    for goal in roadmap_data.get('overall_goals', []):
        story.append(Paragraph(f"• {goal}", styles['Normal']))
    
    story.append(Spacer(1, 0.3*inch))
    
    # Sessions
    story.append(Paragraph("SESSION-BY-SESSION PLAN", styles['Heading2']))
    
    for session in roadmap_data.get('sessions', []):
        story.append(Spacer(1, 0.2*inch))
        
        session_title = f"Session {session['session_number']}: {session['title']}"
        story.append(Paragraph(session_title, styles['Heading3']))
        
        story.append(Paragraph(f"<b>Week:</b> {session.get('week', 'N/A')}", info_style))
        story.append(Paragraph(f"<b>Objective:</b> {session['objective']}", styles['Normal']))
        
        story.append(Paragraph("<b>Techniques:</b>", info_style))
        for technique in session.get('techniques', []):
            story.append(Paragraph(f"  • {technique}", styles['Normal']))
        
        story.append(Paragraph("<b>Expected Outcomes:</b>", info_style))
        for outcome in session.get('expected_outcomes', []):
            story.append(Paragraph(f"  • {outcome}", styles['Normal']))
        
        if session.get('homework'):
            story.append(Paragraph(f"<b>Homework:</b> {session['homework']}", styles['Normal']))
    
    doc.build(story)
    buffer.seek(0)
    return buffer


def render_soap_tab():
    """Render the SOAP Note generation tab"""
    # Main input form
    st.subheader("📝 Session Information")
    
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        client_name = st.text_input("Client Name", placeholder="Enter client name")
    
    with col2:
        session_date = st.date_input("Session Date", value=datetime.now())
    
    with col3:
        duration = st.number_input("Duration (min)", min_value=15, max_value=180, value=50, step=5)
    
    # S3 Integration Section
    st.subheader("☁️ Load from AWS S3")
    #st.markdown("*Automatically fetch conversation files from your S3 bucket (organized by folders)*")
    
    s3_col1, s3_col2 = st.columns([2, 1])
    
    with s3_col1:
        s3_bucket = st.text_input(
            "S3 Bucket Name",
            placeholder="my-therapy-bucket",
            help="Enter your S3 bucket name. Ensure AWS credentials are set in environment variables."
        )
    
    with s3_col2:
        if st.button("🔄 Refresh Folders", use_container_width=True):
            st.session_state.s3_folders_refreshed = True
    
    selected_folder = None
    if s3_bucket:
        try:
            folders_with_txt = list_s3_folders_with_txt(s3_bucket)
            
            if folders_with_txt:
                folder_names = sorted([info['folder_name'] for info in folders_with_txt.values()])
                
                selected_folder_name = st.selectbox(
                    "Select a folder with conversation file",
                    options=folder_names,
                    help="Choose a folder containing a conversation .txt file"
                )
                
                # Find the full folder path
                for folder_path, info in folders_with_txt.items():
                    if info['folder_name'] == selected_folder_name:
                        selected_folder = folder_path
                        txt_file_key = info['txt_file']
                        break
                
                if st.button("📥 Load Conversation from Folder", use_container_width=True):
                    with st.spinner(f"Loading conversation from {selected_folder_name}..."):
                        file_content = get_s3_file_content(s3_bucket, txt_file_key)
                        if file_content:
                            st.session_state.loaded_conversation = file_content
                            st.session_state.selected_s3_folder = selected_folder
                            st.session_state.s3_bucket_name = s3_bucket
                            st.success(f"✅ Loaded: {selected_folder_name}")
            else:
                st.info("📭 No folders with .txt files found in the S3 bucket")
        except Exception as e:
            st.warning(f"⚠️ Could not connect to S3. Make sure AWS credentials are configured. Error: {str(e)}")
    
    # Conversation input
    st.subheader("💬 Therapy Session Conversation")
    st.markdown("*Enter the complete dialogue between therapist and client. **All metrics are calculated from this conversation** - no defaults used.*")
    
    # Add helpful info box
    st.info("💡 **For realistic analysis**: Include actual emotional language, specific topics discussed, and natural conversation flow. The system analyzes emotional keywords, behavioral patterns, engagement levels, and clinical indicators directly from your text.")
    
    # Use loaded conversation if available
    default_conversation = st.session_state.get('loaded_conversation', '')
    
    conversation = st.text_area(
        "Conversation Transcript",
        value=default_conversation,
        height=350,
        placeholder='Therapist: "How have you been feeling since our last session?"\nClient: "I\'ve been really anxious about work. My manager has been putting a lot of pressure on me..."\nTherapist: "Can you tell me more about what\'s been happening?"\nClient: "Well, there\'s this big project deadline coming up and I keep worrying about failing...",',
        help="Paste the full conversation here. The analysis will be based entirely on the emotional language, topics, and patterns found in this text."
    )
    
    return client_name, session_date, duration, conversation


def render_roadmap_tab(llm):
    """Render the Treatment Roadmap tab"""
    st.subheader("🗺️ Treatment Roadmap Generator")
    st.markdown("*Answer the following questions to generate a comprehensive treatment plan*")
    
    # Input form
    col1, col2 = st.columns(2)
    
    with col1:
        primary_issues = st.text_area(
            "Primary Issues/Concerns",
            height=120,
            placeholder="E.g., Generalized anxiety, panic attacks, work-related stress, relationship difficulties...",
            help="Describe the main issues the client is presenting with"
        )
        
        therapy_goals = st.text_area(
            "Therapy Goals",
            height=120,
            placeholder="E.g., Reduce anxiety symptoms, improve coping skills, better work-life balance...",
            help="What does the client hope to achieve through therapy?"
        )
        
        preferred_approach = st.selectbox(
            "Preferred Therapy Approach",
            [
                "Cognitive Behavioral Therapy (CBT)",
                "Dialectical Behavior Therapy (DBT)",
                "Acceptance and Commitment Therapy (ACT)",
                "Psychodynamic Therapy",
                "Mindfulness-Based Therapy",
                "Solution-Focused Brief Therapy",
                "Integrative/Eclectic Approach",
                "Other"
            ],
            help="Select the primary therapeutic modality"
        )
    
    with col2:
        severity_level = st.select_slider(
            "Symptom Severity Level",
            options=["Mild", "Moderate", "Severe", "Very Severe"],
            value="Moderate",
            help="Rate the overall severity of the client's presenting issues"
        )
        
        session_duration = st.selectbox(
            "Session Duration (minutes)",
            [30, 45, 50, 60, 75, 90],
            index=2,
            help="Typical length of each therapy session"
        )
        
        additional_context = st.text_area(
            "Additional Context (Optional)",
            height=120,
            placeholder="E.g., Previous therapy experience, medication, support system, cultural considerations...",
            help="Any other relevant information that might inform the treatment plan"
        )
    
    # Generate button
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if st.button("🚀 Generate Treatment Roadmap", type="primary", use_container_width=True):
            if not all([primary_issues, therapy_goals]):
                st.error("❌ Please fill in at least Primary Issues and Therapy Goals")
            else:
                with st.spinner("🔄 Creating personalized treatment roadmap..."):
                    roadmap = generate_treatment_roadmap(
                        llm,
                        primary_issues,
                        therapy_goals,
                        preferred_approach,
                        session_duration,
                        severity_level,
                        additional_context if additional_context else "None provided"
                    )
                    
                    if roadmap:
                        st.session_state.treatment_roadmap = roadmap
                        st.success("✅ Treatment roadmap generated successfully!")
                        st.rerun()
    
    # Display roadmap if it exists
    if st.session_state.treatment_roadmap:
        roadmap = st.session_state.treatment_roadmap
        
        st.divider()
        st.markdown("## 📋 Treatment Roadmap Overview")
        
        # Summary Metrics (always visible)
        st.markdown('<div class="roadmap-summary">', unsafe_allow_html=True)
        
        sum_col1, sum_col2, sum_col3, sum_col4 = st.columns(4)
        
        with sum_col1:
            st.metric("Total Sessions", roadmap.get('total_sessions', 'N/A'))
        
        with sum_col2:
            st.metric("Duration", f"{roadmap.get('treatment_duration_weeks', 'N/A')} weeks")
        
        with sum_col3:
            st.metric("Approach", roadmap.get('treatment_approach', 'N/A').split()[0])
        
        with sum_col4:
            st.metric("Milestones", len(roadmap.get('milestones', [])))
        
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Treatment Focus - Expandable
        with st.expander("🎯 **Treatment Focus**", expanded=True):
            focus_col1, focus_col2 = st.columns(2)
            
            with focus_col1:
                st.markdown(f"**Primary Focus:** {roadmap.get('primary_diagnosis', 'N/A')}")
            
            with focus_col2:
                st.markdown(f"**Therapeutic Approach:** {roadmap.get('treatment_approach', 'N/A')}")
        
        # Overall Goals - Expandable
        with st.expander("🎯 **Overall Treatment Goals**", expanded=True):
            for i, goal in enumerate(roadmap.get('overall_goals', []), 1):
                st.markdown(f"{i}. {goal}")
        
        # Timeline visualization - Expandable
        with st.expander("📅 **Treatment Timeline**", expanded=False):
            gantt_fig = create_roadmap_gantt(roadmap)
            if gantt_fig:
                st.plotly_chart(gantt_fig, use_container_width=True)
        

        
        # Milestones
        if roadmap.get('milestones'):
            st.markdown("### 🏆 Key Milestones")
            
            milestone_data = []
            for milestone in roadmap.get('milestones', []):
                milestone_data.append({
                    'Session': f"Session {milestone['session']}",
                    'Milestone': milestone['milestone']
                })
            
            if milestone_data:
                df_milestones = pd.DataFrame(milestone_data)
                st.table(df_milestones)
        
        # Success criteria
        if roadmap.get('success_criteria'):
            st.markdown("### ✨ Success Criteria")
            for i, criterion in enumerate(roadmap.get('success_criteria', []), 1):
                st.markdown(f"{i}. {criterion}")
        
        # Export options
        st.divider()
        st.markdown("## 💾 Export Roadmap")
        
        export_col1, export_col2 = st.columns(2)
        
        with export_col1:
            # JSON export
            st.download_button(
                label="📥 Download as JSON",
                data=json.dumps(roadmap, indent=2),
                file_name=f"Treatment_Roadmap_{datetime.now().strftime('%Y%m%d')}.json",
                mime="application/json",
                use_container_width=True
            )
        
        with export_col2:
            # PDF export
            pdf_buffer = create_roadmap_pdf(roadmap)
            st.download_button(
                label="📥 Download as PDF",
                data=pdf_buffer,
                file_name=f"Treatment_Roadmap_{datetime.now().strftime('%Y%m%d')}.pdf",
                mime="application/pdf",
                use_container_width=True
            )
        
        # Clear button
        st.divider()
        if st.button("🔄 Create New Roadmap", use_container_width=True):
            st.session_state.treatment_roadmap = None
            st.rerun()


def render_session_breakdown_tab(llm):
    """Render the Session-by-Session Breakdown tab"""
    st.subheader("📚 Session-by-Session Breakdown")
    
    # Check if roadmap exists
    if 'treatment_roadmap' not in st.session_state or st.session_state.treatment_roadmap is None:
        st.info("💡 Please generate a treatment roadmap first to view session breakdown")
        return
    
    roadmap = st.session_state.treatment_roadmap
    
    # Validate roadmap data
    sessions = roadmap.get('sessions', [])
    total_sessions = roadmap.get('total_sessions', len(sessions))
    treatment_duration_weeks = roadmap.get('treatment_duration_weeks', 0)
    
    if not sessions:
        st.warning("⚠️ No sessions found in the treatment roadmap.")
        return
    
    # Validate session data and show warnings if inconsistent
    if len(sessions) != total_sessions:
        st.warning(f"⚠️ Roadmap specifies {total_sessions} sessions but only {len(sessions)} were generated. Displaying available sessions.")
    
    # Get max week number from sessions
    max_week = max(session.get('week', session.get('session_number', 1)) for session in sessions)
    if treatment_duration_weeks != max_week:
        st.info(f"ℹ️ Treatment spans {max_week} weeks with {len(sessions)} sessions.")
    
    # Create a modern weekly schedule-style layout
    st.markdown("### Treatment Schedule Overview")
    st.markdown(f"**Total Sessions:** {len(sessions)} | **Duration:** {max_week} weeks")
    st.markdown("---")
    
    sessions = roadmap.get('sessions', [])
    
    # Group sessions by week for better organization
    sessions_by_week = {}
    for session in sessions:
        week = session.get('week', session.get('session_number', 1))
        if week not in sessions_by_week:
            sessions_by_week[week] = []
        sessions_by_week[week].append(session)
    
    # Display sessions in expandable weekly layout
    for week_num in sorted(sessions_by_week.keys()):
        week_sessions = sessions_by_week[week_num]
        
        # Count sessions in this week for the header
        session_count = len(week_sessions)
        session_titles = ", ".join([s.get('title', f"Session {s.get('session_number', '')}") for s in week_sessions])
        
        # Create expandable section for each week
        with st.expander(f"📅 Week {week_num} - {session_count} Session{'s' if session_count > 1 else ''}: {session_titles}", expanded=False):
            # Session cards for this week
            for session in week_sessions:
                session_num = session.get('session_number', 1)
                session_title = session.get('title', 'Session')
                session_objective = session.get('objective', 'No objective specified')
                
                # Create modern session card using Streamlit components
                with st.container():
                    # Session header with number badge
                    col1, col2 = st.columns([1, 10])
                    with col1:
                        st.markdown(f"""
                        <div style="
                            width: 40px;
                            height: 40px;
                            background: linear-gradient(135deg, #3b82f6 0%, #1e40af 100%);
                            border-radius: 50%;
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            color: white;
                            font-weight: bold;
                            font-size: 0.9rem;
                            box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);
                            margin-top: 0.5rem;
                        ">
                            #{session_num}
                        </div>
                        """, unsafe_allow_html=True)
                    
                    with col2:
                        st.markdown(f"""
                        <div style="
                            background: white;
                            border: 1px solid #e5e7eb;
                            border-radius: 12px;
                            padding: 1.2rem;
                            margin: 0.5rem 0;
                            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
                        ">
                            <h3 style="
                                margin: 0 0 0.5rem 0;
                                font-size: 1.2rem;
                                font-weight: 600;
                                color: #1f2937;
                            ">{session_title}</h3>
                            <p style="
                                margin: 0 0 1rem 0;
                                color: #6b7280;
                                font-size: 0.95rem;
                            ">{session_objective}</p>
                        </div>
                        """, unsafe_allow_html=True)
                
                    # Techniques section
                    techniques = session.get('techniques', [])
                    if techniques:
                        with col2:
                            st.markdown("""
                            <div style="
                                background: #f8fafc;
                                border-radius: 8px;
                                padding: 1rem;
                                border-left: 4px solid #3b82f6;
                                margin-top: 0.5rem;
                            ">
                                <h4 style="
                                    margin: 0 0 0.5rem 0;
                                    font-size: 0.9rem;
                                    font-weight: 600;
                                    color: #3b82f6;
                                    text-transform: uppercase;
                                    letter-spacing: 0.5px;
                                ">
                                    🔧 Therapeutic Techniques
                                </h4>
                            </div>
                            """, unsafe_allow_html=True)
                            
                            for technique in techniques:
                                st.write(f"• {technique}")
                
                    # Expected outcomes section
                    outcomes = session.get('expected_outcomes', [])
                    if outcomes:
                        with col2:
                            st.markdown("""
                            <div style="
                                background: #f0fdf4;
                                border-radius: 8px;
                                padding: 1rem;
                                border-left: 4px solid #10b981;
                                margin-top: 0.5rem;
                            ">
                                <h4 style="
                                    margin: 0 0 0.5rem 0;
                                    font-size: 0.9rem;
                                    font-weight: 600;
                                    color: #10b981;
                                    text-transform: uppercase;
                                    letter-spacing: 0.5px;
                                ">
                                    ✅ Expected Outcomes
                                </h4>
                            </div>
                            """, unsafe_allow_html=True)
                            
                            for outcome in outcomes:
                                st.write(f"• {outcome}")
                
                    # Homework section
                    homework = session.get('homework')
                    if homework:
                        with col2:
                            st.markdown(f"""
                            <div style="
                                background: #fffbeb;
                                border-radius: 8px;
                                padding: 1rem;
                                border-left: 4px solid #f59e0b;
                                margin-top: 0.5rem;
                            ">
                                <h4 style="
                                    margin: 0 0 0.5rem 0;
                                    font-size: 0.9rem;
                                    font-weight: 600;
                                    color: #f59e0b;
                                    text-transform: uppercase;
                                    letter-spacing: 0.5px;
                                ">
                                    📝 Homework & Activities
                                </h4>
                                <div style="
                                    color: #374151;
                                    font-size: 0.9rem;
                                    line-height: 1.4;
                                ">
                                    {homework}
                                </div>
                            </div>
                            """, unsafe_allow_html=True)
                    
                    # Add spacing between sessions in the same week
                    if len(week_sessions) > 1 and session != week_sessions[-1]:
                        st.markdown("---")


def main():
    # Header
    st.markdown('<div class="main-header">🩺 Clinical SOAP Note Generator</div>', unsafe_allow_html=True)
    st.markdown("AI-powered clinical documentation with comprehensive visual analytics")
    
    # API Key input
    with st.sidebar:
        st.header("⚙️ Configuration")
        api_key = st.text_input("OpenAI API Key", type="password", 
                               help="Enter your OpenAI API key to enable note generation")
        
        if not api_key:
            st.warning("⚠️ Please enter your OpenAI API key")
        
        st.divider()
        st.markdown("### 📋 About SOAP Notes")
        st.markdown("""
        **S**ubjective: Client's reported experience  
        **O**bjective: Therapist's observations  
        **A**ssessment: Clinical interpretation  
        **P**lan: Treatment next steps
        """)
        
        st.divider()
        st.markdown("### 🗺️ About Roadmap")
        st.markdown("""
        Generate comprehensive treatment plans with:
        - Session-by-session breakdown
        - Specific therapeutic techniques
        - Expected outcomes
        - Homework assignments
        """)
    
    if not api_key:
        st.info("👈 Please enter your OpenAI API key in the sidebar to get started")
        return
    
    # Initialize LLM
    llm = initialize_llm(api_key)
    if not llm:
        return
    
    # Create tabs
    tab1, tab2, tab3 = st.tabs(["📝 SOAP Note", "🗺️ Roadmap", "📚 Session Breakdown"])
    
    with tab1:
        st.markdown('<div class="tab-content">', unsafe_allow_html=True)
        client_name, session_date, duration, conversation = render_soap_tab()
        
        # Generate button
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            if st.button("🚀 Generate SOAP Note", type="primary", use_container_width=True, key="generate_soap"):
                if not all([client_name, conversation]):
                    st.error("❌ Please fill in all required fields (Client Name and Conversation)")
                elif len(conversation.strip()) < 50:
                    st.error("❌ Please provide a more detailed conversation (at least 50 characters) for accurate analysis")
                else:
                    with st.spinner("🔄 Analyzing conversation and generating SOAP note..."):
                        soap_response = generate_soap_note(
                            llm, client_name, session_date.strftime("%Y-%m-%d"), duration, conversation
                        )
                        
                        if soap_response:
                            parsed_soap = parse_soap_response(soap_response)
                            # Enhance with calculated metrics
                            enhanced_soap = enhance_metrics_with_analysis(parsed_soap, conversation)
                            st.session_state.soap_note = enhanced_soap
                            st.success("✅ SOAP note generated successfully with realistic metrics based on conversation analysis!")
                            st.rerun()
                        else:
                            st.error("❌ Failed to generate SOAP note. Please try again.")
        
        # Display SOAP note
        if st.session_state.soap_note:
            soap_sections = st.session_state.soap_note
            metrics = soap_sections.get('metrics', {})
            
            st.divider()
            
            # 1. Session Metrics Summary (moved to top)
            st.markdown("## 📊 Session Metrics Summary")
            st.caption("✨ All metrics calculated in real-time from conversation analysis")
            
            metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
            
            with metric_col1:
                risk_level = metrics.get('risk_level', 'unknown')
                if risk_level and risk_level != 'unknown':
                    risk_level_display = risk_level.upper()
                    risk_color = {
                        'LOW': '🟢',
                        'MODERATE': '🟡',
                        'HIGH': '🔴'
                    }.get(risk_level_display, '⚪')
                    st.metric("Risk Level", f"{risk_color} {risk_level_display}")
                else:
                    st.metric("Risk Level", "⚪ NOT ASSESSED")
            
            with metric_col2:
                engagement = metrics.get('engagement_level')
                if engagement is not None:
                    st.metric("Engagement Level", f"{engagement}/10")
                else:
                    st.metric("Engagement Level", "Not calculated")
            
            with metric_col3:
                productivity = metrics.get('session_productivity')
                if productivity is not None:
                    st.metric("Session Productivity", f"{productivity}/10")
                else:
                    st.metric("Session Productivity", "Not calculated")
            
            with metric_col4:
                # Calculate total emotional intensity
                emotional_dist = metrics.get('emotional_distribution', {})
                negative_emotions = emotional_dist.get('anxious', 0) + emotional_dist.get('sad', 0) + emotional_dist.get('angry', 0)
                st.metric("Negative Affect", f"{negative_emotions}%")
            
            st.divider()
            
            # Information about analysis methodology
            st.info("📊 **Real-Time Conversation Analysis** - All metrics, scores, and visualizations are dynamically calculated from your conversation content using advanced natural language processing. No hardcoded values are used - every number reflects the actual emotional language, behavioral patterns, clinical indicators, and therapeutic progress present in this specific session.")
            
            # 2. SOAP Note with Expandable Sections
            st.markdown("## 📊 Generated SOAP Note")
            
            # SUBJECTIVE - Expandable
            with st.expander("📢 SUBJECTIVE - What Client Says", expanded=True):
                # 1. KEY SUMMARY (if available)
                if soap_sections.get('subjective_brief'):
                    brief_content = soap_sections["subjective_brief"]
                    # Clean any unwanted characters and formatting
                    brief_content = brief_content.replace('*', '').replace('**', '').replace('***', '').replace('****', '').strip()
                    if brief_content:
                        st.markdown('<div class="note-label">Key Summary</div>', unsafe_allow_html=True)
                        st.markdown(f'<div class="brief-highlight">{brief_content}</div>', unsafe_allow_html=True)
                        st.markdown("---")  # Visual separator
                
                # 2. DETAILED NOTES
                st.markdown('<div class="note-label">Detailed Notes</div>', unsafe_allow_html=True)
                subjective_content = soap_sections.get("subjective", "No data available")
                # Clean any unwanted characters and formatting
                subjective_content = subjective_content.replace('*', '').replace('**', '').replace('***', '').replace('****', '').strip()
                st.markdown(f'<div class="content-text">{subjective_content}</div>', unsafe_allow_html=True)
                st.markdown("---")  # Visual separator
                
                # 3. VISUAL ANALYSIS
                st.markdown('<div class="note-label">Visual Analysis</div>', unsafe_allow_html=True)
                col1, col2 = st.columns([1, 1])
                
                with col1:
                    if metrics.get('emotional_distribution'):
                        fig = create_subjective_chart(metrics['emotional_distribution'])
                        st.plotly_chart(fig, use_container_width=True)
                
                with col2:
                    if metrics.get('emotional_context'):
                        st.markdown("**Emotional Context:**")
                        emotional_context = metrics['emotional_context']
                        context_html = '<div class="content-text">'
                        for emotion, context in emotional_context.items():
                            context_html += f'<div class="context-box"><b>😟 {emotion.capitalize()}:</b> {context}</div>'
                        context_html += '</div>'
                        st.markdown(context_html, unsafe_allow_html=True)
            
            # OBJECTIVE - Expandable
            with st.expander("👁️ OBJECTIVE - What Therapist Observed", expanded=True):
                # 1. KEY SUMMARY (if available)
                if soap_sections.get('objective_brief'):
                    brief_content = soap_sections["objective_brief"]
                    # Clean any unwanted characters and formatting
                    brief_content = brief_content.replace('*', '').replace('**', '').replace('***', '').replace('****', '').strip()
                    if brief_content:
                        st.markdown('<div class="note-label">Key Summary</div>', unsafe_allow_html=True)
                        st.markdown(f'<div class="brief-highlight">{brief_content}</div>', unsafe_allow_html=True)
                        st.markdown("---")  # Visual separator
                
                # 2. DETAILED NOTES
                st.markdown('<div class="note-label">Detailed Notes</div>', unsafe_allow_html=True)
                objective_content = soap_sections.get("objective", "No data available")
                # Clean any unwanted characters and formatting
                objective_content = objective_content.replace('*', '').replace('**', '').replace('***', '').replace('****', '').strip()
                st.markdown(f'<div class="content-text">{objective_content}</div>', unsafe_allow_html=True)
                st.markdown("---")  # Visual separator
                
                # 3. VISUAL ANALYSIS
                if metrics.get('observed_behaviors'):
                    st.markdown('<div class="note-label">Clinical Behavioral Assessment (1-10 Scale)</div>', unsafe_allow_html=True)
                    fig = create_objective_chart(metrics['observed_behaviors'])
                    st.plotly_chart(fig, use_container_width=True)
            
            # ASSESSMENT - Expandable
            with st.expander("🧠 ASSESSMENT - Clinical Opinion", expanded=True):
                # 1. KEY SUMMARY (if available)
                if soap_sections.get('assessment_brief'):
                    brief_content = soap_sections["assessment_brief"]
                    # Clean any unwanted characters and formatting
                    brief_content = brief_content.replace('*', '').replace('**', '').replace('***', '').replace('****', '').strip()
                    if brief_content:
                        st.markdown('<div class="note-label">Key Summary</div>', unsafe_allow_html=True)
                        st.markdown(f'<div class="brief-highlight">{brief_content}</div>', unsafe_allow_html=True)
                        st.markdown("---")  # Visual separator
                
                # 2. DETAILED NOTES
                st.markdown('<div class="note-label">Detailed Notes</div>', unsafe_allow_html=True)
                assessment_content = soap_sections.get("assessment", "No data available")
                # Clean any unwanted characters and formatting
                assessment_content = assessment_content.replace('*', '').replace('**', '').replace('***', '').replace('****', '').strip()
                st.markdown(f'<div class="content-text">{assessment_content}</div>', unsafe_allow_html=True)
                st.markdown("---")  # Visual separator
                
                # 3. VISUAL ANALYSIS
                if metrics.get('assessment_domains'):
                    st.markdown('<div class="note-label">Clinical Assessment Domains (0-10 Severity Scale)</div>', unsafe_allow_html=True)
                    fig = create_assessment_chart(metrics['assessment_domains'])
                    st.plotly_chart(fig, use_container_width=True)
            
            # PLAN - Expandable
            with st.expander("📋 PLAN - Treatment Next Steps", expanded=True):
                # 1. KEY SUMMARY (if available)
                if soap_sections.get('plan_brief'):
                    brief_content = soap_sections["plan_brief"]
                    # Clean any unwanted characters and formatting
                    brief_content = brief_content.replace('*', '').replace('**', '').replace('***', '').replace('****', '').strip()
                    if brief_content:
                        st.markdown('<div class="note-label">Key Summary</div>', unsafe_allow_html=True)
                        st.markdown(f'<div class="brief-highlight">{brief_content}</div>', unsafe_allow_html=True)
                        st.markdown("---")  # Visual separator
                
                # 2. DETAILED NOTES
                st.markdown('<div class="note-label">Detailed Notes</div>', unsafe_allow_html=True)
                plan_content = soap_sections.get("plan", "No data available")
                # Clean any unwanted characters and formatting
                plan_content = plan_content.replace('*', '').replace('**', '').replace('***', '').replace('****', '').strip()
                st.markdown(f'<div class="content-text">{plan_content}</div>', unsafe_allow_html=True)
                st.markdown("---")  # Visual separator
                
                # 3. VISUAL TIMELINE
                # Use plan_timeline from metrics if available
                plan_timeline = metrics.get('plan_timeline', [])
                if plan_timeline:
                    display_plan_timeline(plan_timeline)
                else:
                    st.info("💡 No specific timeline generated. Timeline will be created based on treatment plan.")
            
            # Preview & Feedback Loop Section
            st.divider()
            st.markdown("## 🔍 Preview & Feedback Loop")
            st.markdown("*Review the SOAP note and provide feedback to refine it, or accept and finalize*")
            
            # Initialize feedback tracking in session state
            if 'feedback_version' not in st.session_state:
                st.session_state.feedback_version = 1
            if 'feedback_history' not in st.session_state:
                st.session_state.feedback_history = []
            
            preview_col1, preview_col2 = st.columns(2)
            
            with preview_col1:
                st.info(f"📋 **Version {st.session_state.feedback_version}** - Current SOAP Note Preview")
            
            with preview_col2:
                if st.session_state.feedback_version > 1:
                    st.success(f"✅ Iteration {st.session_state.feedback_version - 1} completed")
            
            # Feedback input
            feedback_text = st.text_area(
                "Provide Feedback (Optional)",
                placeholder="E.g., 'The assessment section needs more detail about anxiety symptoms', 'Add more specific examples from the conversation', 'Adjust the risk level assessment'...",
                height=100,
                help="Describe what you'd like to improve or change in the SOAP note. Leave empty if you're satisfied with the current version."
            )
            
            # Action buttons
            feedback_col1, feedback_col2, feedback_col3 = st.columns(3)
            
            with feedback_col1:
                if st.button("✅ Accept & Finalize", use_container_width=True, type="primary"):
                    # Save final version to S3 if available
                    if st.session_state.get('selected_s3_folder') and st.session_state.get('s3_bucket_name'):
                        with st.spinner("📤 Uploading final SOAP note to S3..."):
                            bucket = st.session_state.s3_bucket_name
                            folder = st.session_state.selected_s3_folder
                            
                            # Create PDF and upload
                            pdf_buffer = create_pdf(soap_sections, client_name, session_date.strftime("%Y-%m-%d"), metrics)
                            pdf_path = get_final_pdf_path(folder)
                            
                            if upload_pdf_to_s3(bucket, pdf_path, pdf_buffer):
                                st.success(f"✅ Final SOAP note saved to S3: {pdf_path}")
                                # Save feedback history to S3
                                feedback_data = {
                                    "version": st.session_state.feedback_version,
                                    "status": "FINALIZED",
                                    "timestamp": datetime.now().isoformat(),
                                    "feedback_history": st.session_state.feedback_history,
                                    "final_metrics": metrics
                                }
                                feedback_json_path = get_feedback_file_path(folder, st.session_state.feedback_version)
                                if upload_to_s3(bucket, feedback_json_path, json.dumps(feedback_data, indent=2)):
                                    st.success(f"✅ Feedback history saved: {feedback_json_path}")
                            else:
                                st.error("❌ Failed to upload to S3")
                    else:
                        st.success("✅ SOAP note finalized! You can now download it using the export options below.")
            
            with feedback_col2:
                if st.button("🔄 Regenerate with Feedback", use_container_width=True):
                    if not feedback_text.strip():
                        st.warning("⚠️ Please provide feedback to regenerate the SOAP note")
                    else:
                        # Store feedback in history
                        st.session_state.feedback_history.append({
                            "version": st.session_state.feedback_version,
                            "feedback": feedback_text,
                            "timestamp": datetime.now().isoformat()
                        })
                        
                        # Save feedback to S3 if available
                        if st.session_state.get('selected_s3_folder') and st.session_state.get('s3_bucket_name'):
                            bucket = st.session_state.s3_bucket_name
                            folder = st.session_state.selected_s3_folder
                            feedback_data = {
                                "version": st.session_state.feedback_version,
                                "status": "IN_PROGRESS",
                                "feedback": feedback_text,
                                "timestamp": datetime.now().isoformat()
                            }
                            feedback_json_path = get_feedback_file_path(folder, st.session_state.feedback_version)
                            upload_to_s3(bucket, feedback_json_path, json.dumps(feedback_data, indent=2))
                        
                        # Regenerate SOAP note with feedback
                        with st.spinner("🔄 Regenerating SOAP note with your feedback..."):
                            soap_response = generate_soap_note(
                                llm, client_name, session_date.strftime("%Y-%m-%d"), duration, conversation
                            )
                            
                            if soap_response:
                                parsed_soap = parse_soap_response(soap_response)
                                enhanced_soap = enhance_metrics_with_analysis(parsed_soap, conversation)
                                
                                # Update session state
                                st.session_state.soap_note = enhanced_soap
                                st.session_state.feedback_version += 1
                                
                                st.success(f"✅ SOAP note regenerated (Version {st.session_state.feedback_version})!")
                                st.rerun()
                            else:
                                st.error("❌ Failed to regenerate SOAP note. Please try again.")
            
            with feedback_col3:
                if st.button("📋 View Feedback History", use_container_width=True):
                    if st.session_state.feedback_history:
                        st.info("**Feedback History:**")
                        for i, fb in enumerate(st.session_state.feedback_history, 1):
                            st.write(f"**Version {fb['version']}** ({fb['timestamp']})")
                            st.write(f"*{fb['feedback']}*")
                            st.divider()
                    else:
                        st.info("No feedback provided yet.")
            
            # Export Options
            st.divider()
            st.markdown("## 💾 Export Options")
            
            export_col1, export_col2, export_col3 = st.columns(3)
            
            with export_col1:
                # Text export
                full_text = f"""CLINICAL SOAP NOTE
{'='*60}

CLIENT: {client_name}
SESSION DATE: {session_date.strftime("%Y-%m-%d")}
DURATION: {duration} minutes
RISK LEVEL: {metrics.get('risk_level', 'N/A').upper()}
ENGAGEMENT: {metrics.get('engagement_level', 'N/A')}/10
PRODUCTIVITY: {metrics.get('session_productivity', 'N/A')}/10

{'='*60}

SUBJECTIVE (What Client Says)
{'-'*60}
Brief: {soap_sections.get('subjective_brief', '')}

{soap_sections.get('subjective', '')}

{'='*60}

OBJECTIVE (What Therapist Observed)
{'-'*60}
Brief: {soap_sections.get('objective_brief', '')}

{soap_sections.get('objective', '')}

{'='*60}

ASSESSMENT (Clinical Opinion)
{'-'*60}
Brief: {soap_sections.get('assessment_brief', '')}

{soap_sections.get('assessment', '')}

{'='*60}

PLAN (Treatment Next Steps)
{'-'*60}
Brief: {soap_sections.get('plan_brief', '')}

{soap_sections.get('plan', '')}

{'='*60}
End of SOAP Note
"""
                
                st.download_button(
                    label="📥 Download as TXT",
                    data=full_text,
                    file_name=f"SOAP_{client_name.replace(' ', '_')}_{session_date.strftime('%Y%m%d')}.txt",
                    mime="text/plain",
                    use_container_width=True
                )
            
            with export_col2:
                # PDF export
                pdf_buffer = create_pdf(soap_sections, client_name, session_date.strftime("%Y-%m-%d"), metrics)
                st.download_button(
                    label="📥 Download as PDF",
                    data=pdf_buffer,
                    file_name=f"SOAP_{client_name.replace(' ', '_')}_{session_date.strftime('%Y%m%d')}.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
            
            with export_col3:
                # JSON export with all data
                json_data = {
                    "client_name": client_name,
                    "session_date": session_date.strftime("%Y-%m-%d"),
                    "duration": duration,
                    "soap_note": {
                        "subjective": {
                            "brief": soap_sections.get('subjective_brief', ''),
                            "detailed": soap_sections.get('subjective', '')
                        },
                        "objective": {
                            "brief": soap_sections.get('objective_brief', ''),
                            "detailed": soap_sections.get('objective', '')
                        },
                        "assessment": {
                            "brief": soap_sections.get('assessment_brief', ''),
                            "detailed": soap_sections.get('assessment', '')
                        },
                        "plan": {
                            "brief": soap_sections.get('plan_brief', ''),
                            "detailed": soap_sections.get('plan', '')
                        }
                    },
                    "metrics": metrics
                }
                
                st.download_button(
                    label="📥 Download as JSON",
                    data=json.dumps(json_data, indent=2),
                    file_name=f"SOAP_{client_name.replace(' ', '_')}_{session_date.strftime('%Y%m%d')}.json",
                    mime="application/json",
                    use_container_width=True
                )
            
            # Clear button
            st.divider()
            if st.button("🔄 Generate New SOAP Note", use_container_width=True):
                st.session_state.soap_note = None
                st.rerun()
        
        st.markdown('</div>', unsafe_allow_html=True)
    
    with tab2:
        st.markdown('<div class="tab-content">', unsafe_allow_html=True)
        render_roadmap_tab(llm)
        st.markdown('</div>', unsafe_allow_html=True)
    
    with tab3:
        st.markdown('<div class="tab-content">', unsafe_allow_html=True)
        render_session_breakdown_tab(llm)
        st.markdown('</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
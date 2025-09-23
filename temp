<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&display=swap" rel="stylesheet">
    
    <link rel="stylesheet" href="{{ url_for('static', filename='css/styles.css') }}">
    
    <title>{% block title %}kAIzen Coach{% endblock %}</title>
</head>

<body class="bg-brand-dark text-brand-light-gray font-sans antialiased flex flex-col min-h-screen"> 
    
    <div class="max-w-4xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
    
        <header class="flex justify-between items-center mb-10">
            <div>
                {% if request.endpoint == 'index' %}
                {% else %}
                    <a href="/" class="block">
                        <img src="{{ url_for('static', filename='images/logo.jpg') }}" alt="kAIzen Coach Logo" class="h-12 w-auto">
                    </a>
                 {% endif %}
            </div>
        
            <div>
                {% if athlete %}
                    <img src="{{ athlete.profile }}" alt="Athlete profile picture" class="h-30 w-30 rounded-full border-2 border-brand-blue">
                {% endif %}
            </div>
        </header>

        {% if athlete and request.endpoint != 'index' %}
            {% include '_navigation.html' %}
        {% endif %}

        <main class="flex-grow">
            {% block content %}{% endblock %}
        </main>

    </div>

    <footer class="w-full text-center text-gray-400 text-sm py-6">
        <div class="max-w-4xl mx-auto border-t border-brand-gray pt-6">
            {% if athlete %}
            <div class="flex justify-center items-center space-x-4">
                <a href="/onboarding" class="hover:text-brand-blue transition-colors">Generate New Plan</a>
                <span>|</span>
                <a href="/delete_data" class="hover:text-brand-blue transition-colors" onclick="return confirm('Are you sure you want to permanently delete all your data? This cannot be undone.');">Delete My Data</a>
                <span>|</span>
                <a href="/logout" class="hover:text-brand-blue transition-colors">Logout</a>
            </div>
            {% endif %}
        </div>
    </footer>

</body>
</html>
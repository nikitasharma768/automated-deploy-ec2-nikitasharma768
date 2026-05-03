from flask import Flask, jsonify, render_template

app = Flask(__name__, static_folder='static', template_folder='templates')


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/message')
def message():
    return jsonify({"message": "Hello from Flask on EC2!"})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)

#!/usr/bin/env python3
"""
Display Control REPL for macOS

A command-line interface to control display power state on macOS using native tools.
Supports turning displays on/off, checking status, and preventing sleep.
"""

import subprocess
import json
import time
import signal
import sys
from typing import Optional, List, Dict, Any

# Global state
displays: List[Dict[str, Any]] = []
caffeinate_process: Optional[subprocess.Popen] = None
running = True


def verify_tools() -> None:
    """Check if required macOS tools are available"""
    tools = ['pmset', 'caffeinate', 'system_profiler']
    for tool in tools:
        if not command_exists(tool):
            raise RuntimeError(f"Required tool '{tool}' not found. This script requires macOS.")


def command_exists(command: str) -> bool:
    """Check if a command exists on the system"""
    try:
        result = subprocess.run(['which', command], capture_output=True, text=True)
        return result.returncode == 0
    except subprocess.SubprocessError:
        return False


def safe_execute(cmd: List[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """Execute command with error handling"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)
        return result
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nError: {e.stderr}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Command timeout: {' '.join(cmd)}")


def scan_displays() -> None:
    """Use system_profiler to detect connected displays"""
    global displays
    try:
        cmd = ['system_profiler', 'SPDisplaysDataType', '-json']
        result = safe_execute(cmd)
        data = json.loads(result.stdout)
        
        displays = []
        
        # Parse display data from system_profiler JSON output
        for gpu_data in data.get('SPDisplaysDataType', []):
            ndrvs = gpu_data.get('spdisplays_ndrvs', [])
            for display in ndrvs:
                display_info = {
                    'name': display.get('_name', 'Unknown Display'),
                    'vendor_id': display.get('_spdisplays_display-vendor-id', 'Unknown'),
                    'product_id': display.get('_spdisplays_display-product-id', 'Unknown'),
                    'resolution': display.get('_spdisplays_resolution', 'Unknown'),
                    'pixels': display.get('_spdisplays_pixels', 'Unknown'),
                    'connection_type': display.get('spdisplays_connection_type', 'Unknown'),
                    'display_type': display.get('spdisplays_display_type', 'Unknown'),
                    'main': display.get('spdisplays_main', 'spdisplays_no') == 'spdisplays_yes',
                    'online': display.get('spdisplays_online', 'spdisplays_no') == 'spdisplays_yes',
                    'display_id': display.get('_spdisplays_displayID', 'Unknown')
                }
                displays.append(display_info)
                
    except (json.JSONDecodeError, KeyError) as e:
        raise RuntimeError(f"Failed to parse display information: {e}")


def display_on() -> bool:
    """Turn display on using caffeinate -u"""
    try:
        # caffeinate -u -t 5 turns display on and keeps active for 5 seconds
        # This creates a user activity assertion that wakes the display
        cmd = ['caffeinate', '-u', '-t', '5']
        result = safe_execute(cmd, timeout=15)
        
        # Give the display a moment to wake up
        time.sleep(1)
        return True
        
    except RuntimeError as e:
        print(f"Failed to turn display on: {e}")
        return False


def display_off() -> bool:
    """Turn display off using pmset displaysleepnow"""
    try:
        # pmset displaysleepnow puts all displays to sleep immediately
        cmd = ['pmset', 'displaysleepnow']
        safe_execute(cmd)
        return True
        
    except RuntimeError as e:
        print(f"Failed to turn display off: {e}")
        return False


def get_display_state() -> Dict[str, Any]:
    """Check current display state using pmset assertions"""
    global caffeinate_process
    try:
        # Get current power management assertions
        cmd = ['pmset', '-g', 'assertions']
        result = safe_execute(cmd)
        output = result.stdout
        
        # Parse assertion state
        state = {
            'displays_found': len(displays),
            'display_count': len([d for d in displays if d['online']]),
            'user_active': 'UserIsActive                   1' in output,
            'prevent_display_sleep': 'PreventUserIdleDisplaySleep' in output,
            'caffeinate_running': caffeinate_process is not None
        }
        
        return state
        
    except RuntimeError as e:
        print(f"Failed to get display state: {e}")
        return {'error': str(e)}


def prevent_sleep(duration: Optional[int] = None) -> bool:
    """Prevent display sleep using caffeinate -d"""
    global caffeinate_process
    try:
        # Stop any existing caffeinate process
        if caffeinate_process:
            stop_sleep_prevention()
        
        # Build caffeinate command
        cmd = ['caffeinate', '-d']  # Prevent display sleep
        
        if duration:
            cmd.extend(['-t', str(duration)])
        
        # Start caffeinate process in background
        caffeinate_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Give it a moment to start
        time.sleep(0.5)
        
        # Check if process is still running
        if caffeinate_process.poll() is None:
            return True
        else:
            caffeinate_process = None
            return False
            
    except Exception as e:
        print(f"Failed to start sleep prevention: {e}")
        caffeinate_process = None
        return False


def stop_sleep_prevention() -> bool:
    """Stop sleep prevention by terminating caffeinate process"""
    global caffeinate_process
    if caffeinate_process:
        try:
            caffeinate_process.terminate()
            caffeinate_process.wait(timeout=5)
            caffeinate_process = None
            return True
        except subprocess.TimeoutExpired:
            # Process didn't terminate, force kill it
            if caffeinate_process:
                try:
                    caffeinate_process.kill()
                    caffeinate_process.wait(timeout=5)
                except (subprocess.TimeoutExpired, AttributeError):
                    pass  # Process is likely already dead
            caffeinate_process = None
            return True
        except Exception as e:
            print(f"Failed to stop sleep prevention: {e}")
            caffeinate_process = None
            return False
    return True


def refresh_displays() -> None:
    """Re-scan for connected displays"""
    scan_displays()


def get_display_info() -> List[Dict[str, Any]]:
    """Get detailed information about all displays"""
    return displays.copy()


def signal_handler(signum, frame):
    """Handle signals for clean shutdown"""
    global running
    print("\nShutting down...")
    stop_sleep_prevention()
    running = False


def handle_help() -> None:
    """Display help information"""
    help_text = """
Available commands:
  on                    Turn display on
  off                   Turn display off
  status                Show current display state
  scan                  Re-scan for connected displays
  info                  Show detailed display information
  prevent [seconds]     Prevent display sleep (optional duration)
  stop-prevent          Stop sleep prevention
  help                  Show this help message
  quit                  Exit the REPL

Examples:
  > on                  # Turn display on
  > off                 # Turn display off
  > prevent 300         # Prevent sleep for 5 minutes
  > prevent             # Prevent sleep indefinitely
  > stop-prevent        # Stop sleep prevention
        """
    print(help_text)


def handle_display_on() -> None:
    """Handle display on command"""
    print("Turning display on...")
    if display_on():
        print("Display turned on successfully")
    else:
        print("Failed to turn display on")


def handle_display_off() -> None:
    """Handle display off command"""
    print("Turning display off...")
    if display_off():
        print("Display turned off successfully")
    else:
        print("Failed to turn display off")


def handle_status() -> None:
    """Handle status command"""
    state = get_display_state()
    
    print("\n=== Display Status ===")
    print(f"Displays found: {state.get('displays_found', 'Unknown')}")
    print(f"Displays online: {state.get('display_count', 'Unknown')}")
    print(f"User active: {'Yes' if state.get('user_active') else 'No'}")
    print(f"Display sleep prevented: {'Yes' if state.get('prevent_display_sleep') else 'No'}")
    print(f"Sleep prevention active: {'Yes' if state.get('caffeinate_running') else 'No'}")
    print()


def handle_scan() -> None:
    """Handle scan command"""
    print("Scanning for displays...")
    refresh_displays()
    display_list = get_display_info()
    print(f"Found {len(display_list)} display(s)")


def handle_prevent(args: List[str]) -> None:
    """Handle prevent command"""
    duration = None
    if args:
        try:
            duration = int(args[0])
            print(f"Preventing display sleep for {duration} seconds...")
        except ValueError:
            print("Invalid duration. Usage: prevent <seconds>")
            return
    else:
        print("Preventing display sleep indefinitely...")
    
    if prevent_sleep(duration):
        if duration:
            print(f"Display sleep prevention started for {duration} seconds")
        else:
            print("Display sleep prevention started indefinitely")
    else:
        print("Failed to start sleep prevention")


def handle_stop_prevent() -> None:
    """Handle stop-prevent command"""
    print("Stopping sleep prevention...")
    if stop_sleep_prevention():
        print("Sleep prevention stopped")
    else:
        print("Failed to stop sleep prevention")


def handle_info() -> None:
    """Handle info command"""
    display_list = get_display_info()
    
    if not display_list:
        print("No displays found")
        return
    
    print(f"\n=== Display Information ===")
    for i, display in enumerate(display_list, 1):
        print(f"\nDisplay {i}:")
        print(f"  Name: {display['name']}")
        print(f"  Resolution: {display['resolution']}")
        print(f"  Pixels: {display['pixels']}")
        print(f"  Connection: {display['connection_type']}")
        print(f"  Type: {display['display_type']}")
        print(f"  Main display: {'Yes' if display['main'] else 'No'}")
        print(f"  Online: {'Yes' if display['online'] else 'No'}")
    print()


def handle_quit() -> None:
    """Handle quit command"""
    global running
    print("Goodbye!")
    running = False


def start_repl() -> None:
    """Start the interactive REPL"""
    global running
    print("Display Control REPL started")
    print("Type 'help' for available commands or 'quit' to exit")
    
    while running:
        try:
            user_input = input("> ").strip()
            if not user_input:
                continue
            
            parts = user_input.split()
            command = parts[0].lower()
            
            if command == "quit" or command == "exit":
                handle_quit()
            elif command == "help":
                handle_help()
            elif command == "on":
                handle_display_on()
            elif command == "off":
                handle_display_off()
            elif command == "status":
                handle_status()
            elif command == "scan":
                handle_scan()
            elif command == "prevent":
                handle_prevent(parts[1:])
            elif command == "stop" or command == "stop-prevent":
                handle_stop_prevent()
            elif command == "info":
                handle_info()
            else:
                print(f"Unknown command: {command}")
                print("Type 'help' for available commands")
                
        except EOFError:
            print("\nExiting...")
            break
        except KeyboardInterrupt:
            print("\nUse 'quit' to exit")
        except Exception as e:
            print(f"Error: {e}")
    
    # Clean up on exit
    stop_sleep_prevention()


def main():
    """Main entry point"""
    try:
        # Set up signal handlers for clean exit
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Initialize
        verify_tools()
        scan_displays()
        
        # Prevent sleep by default
        print("Preventing display sleep by default...")
        if prevent_sleep():
            print("Sleep prevention started")
        else:
            print("Failed to start sleep prevention")
        
        start_repl()
    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
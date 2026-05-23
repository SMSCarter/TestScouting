import React from 'react';
import Svg, { Circle, Path, G, Text, Defs, TextPath } from 'react-native-svg';

export default function Logo({ size = 150 }) {
  // SVG scales perfectly using viewBox
  return (
    <Svg
      width={size}
      height={size}
      viewBox="0 0 400 400"
      style={{ alignSelf: 'center' }}
    >
      <Defs>
        {/* Paths for text curvature */}
        <Path
          id="textPathTop"
          d="M 60,200 A 140,140 0 0,1 340,200"
          fill="none"
        />
        <Path
          id="textPathBottom"
          d="M 340,200 A 140,140 0 0,1 60,200"
          fill="none"
        />
      </Defs>

      {/* Outer black border */}
      <Circle cx="200" cy="200" r="195" fill="#0D1117" stroke="#1E56A0" strokeWidth="6" />

      {/* Main blue circle background */}
      <Circle cx="200" cy="200" r="180" fill="#1E56A0" stroke="#FFFFFF" strokeWidth="4" />

      {/* Inner Black Gear Background */}
      <G transform="translate(200, 200)">
        {/* Center circle of the gear */}
        <Circle cx="0" cy="0" r="95" fill="#0D1117" />
        
        {/* Gear Teeth (10 teeth) */}
        {[0, 36, 72, 108, 144, 180, 216, 252, 288, 324].map((angle) => (
          <G key={angle} transform={`rotate(${angle})`}>
            <Path
              d="M -22,-85 L -16,-115 L 16,-115 L 22,-85 Z"
              fill="#0D1117"
            />
          </G>
        ))}

        {/* Core of the gear */}
        <Circle cx="0" cy="0" r="75" fill="#0D1117" />
      </G>

      {/* Stylized White "S" and Saber in the center */}
      <G transform="translate(200, 200) scale(1.1)">
        {/* White "S" */}
        <Path
          d="M -30,-40 C -30,-40 -10,-48 10,-48 C 30,-48 38,-38 38,-28 C 38,-10 0,-15 0,0 C 0,15 35,10 35,28 C 35,46 15,48 -5,48 C -25,48 -35,40 -35,40 L -30,22 C -30,22 -15,28 0,28 C 15,28 18,22 18,14 C 18,2 -18,8 -18,-14 C -18,-30 10,-30 10,-30 L -30,-40 Z"
          fill="#FFFFFF"
        />

        {/* Silver/White Saber Sword running diagonally across the S */}
        <Path
          d="M -60,20 L 60,-25 L 35,-15 L -60,20"
          fill="#E2E8F0"
          stroke="#0D1117"
          strokeWidth="2"
        />
        {/* Saber Handle/Hilt */}
        <Path
          d="M -62,18 C -68,22 -72,28 -68,32 C -64,36 -58,32 -54,28"
          fill="none"
          stroke="#FFFFFF"
          strokeWidth="4"
        />
        <Path
          d="M -56,22 L -50,28"
          stroke="#FFFFFF"
          strokeWidth="3"
        />
      </G>

      {/* Text on top curve: SABRE ROBOTICS */}
      <Text fill="#FFFFFF" fontSize="30" fontWeight="900" fontFamily="sans-serif">
        <TextPath href="#textPathTop" startOffset="50%" textAnchor="middle">
          SABRE ROBOTICS
        </TextPath>
      </Text>

      {/* Text on bottom curve: SARTELL, MN 6045 */}
      <Text fill="#FFFFFF" fontSize="26" fontWeight="900" fontFamily="sans-serif">
        <TextPath href="#textPathBottom" startOffset="50%" textAnchor="middle">
          SARTELL, MN 6045
        </TextPath>
      </Text>
    </Svg>
  );
}
